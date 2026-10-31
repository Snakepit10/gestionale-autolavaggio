"""Test della contabilita': lettura FatturaPA (XML, p7m, ZIP), import
idempotente, PEC, classificazione e pagine. Solo dati inventati."""
import base64
import io
import zipfile
from datetime import date
from decimal import Decimal
from email.message import EmailMessage

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.magazzino.models import Fornitore

from .fatturapa import ErroreFattura, e_fattura, estrai_xml, leggi_fattura, xml_da_p7m
from .importa import importa_file
from .models import (FatturaPassiva, MessaggioPec, RegolaFornitore, ScadenzaFattura, VoceConto,
                     ripartizione_valida)
from .pec import importa_messaggio


def fattura_xml(numero='12/A', tipo='TD01', piva='01234567890', nome='Detergenti Srl',
                righe=(('Shampoo auto', '100.00', '22.00'), ('Cera', '50.004', '22.00')),
                imponibile='150.00', imposta='33.00', totale='183.00', pagamenti=(('2026-11-30', '183.00'),)):
    linee = ''.join(
        f'<DettaglioLinee><NumeroLinea>{i}</NumeroLinea><Descrizione>{d}</Descrizione>'
        f'<PrezzoTotale>{p}</PrezzoTotale><AliquotaIVA>{a}</AliquotaIVA></DettaglioLinee>'
        for i, (d, p, a) in enumerate(righe, 1))
    pag = ''.join(
        f'<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>'
        f'<DataScadenzaPagamento>{d}</DataScadenzaPagamento><ImportoPagamento>{i}</ImportoPagamento>'
        f'<IBAN>IT00X0000000000000000000000</IBAN></DettaglioPagamento>' for d, i in pagamenti)
    totale_xml = f'<ImportoTotaleDocumento>{totale}</ImportoTotaleDocumento>' if totale else ''
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:FatturaElettronica versione="FPR12" xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">'
        '<FatturaElettronicaHeader><CedentePrestatore><DatiAnagrafici>'
        f'<IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>{piva}</IdCodice></IdFiscaleIVA>'
        f'<Anagrafica><Denominazione>{nome}</Denominazione></Anagrafica>'
        '</DatiAnagrafici></CedentePrestatore></FatturaElettronicaHeader>'
        '<FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>'
        f'<TipoDocumento>{tipo}</TipoDocumento><Divisa>EUR</Divisa><Data>2026-10-15</Data>'
        f'<Numero>{numero}</Numero>{totale_xml}<Causale>Fornitura ottobre</Causale>'
        '</DatiGeneraliDocumento></DatiGenerali>'
        f'<DatiBeniServizi>{linee}<DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA>'
        f'<ImponibileImporto>{imponibile}</ImponibileImporto><Imposta>{imposta}</Imposta>'
        '</DatiRiepilogo></DatiBeniServizi>'
        f'<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>{pag}</DatiPagamento>'
        '</FatturaElettronicaBody></p:FatturaElettronica>').encode()


def _der(tag, contenuto):
    n = len(contenuto)
    if n < 0x80:
        lung = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        lung = bytes([0x80 | len(b)]) + b
    return bytes([tag]) + lung + contenuto


def p7m(xml, pezzo=40):
    """Busta CAdES finta: SignedData con il contenuto in una OCTET STRING
    spezzata a lunghezza indefinita (come fanno molti software di firma)."""
    pezzi = b''.join(_der(0x04, xml[i:i + pezzo]) for i in range(0, len(xml), pezzo))
    contenuto = b'\x24\x80' + pezzi + b'\x00\x00'
    encap = _der(0x30, _der(0x06, b'\x2a\x86\x48\x86\xf7\x0d\x01\x07\x01') + b'\xa0\x80' + contenuto + b'\x00\x00')
    signed = _der(0x30, _der(0x02, b'\x01') + _der(0x31, b'') + encap + _der(0x31, b'firma'))
    return _der(0x30, _der(0x06, b'\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02') + _der(0xA0, signed))


class FatturaPATest(TestCase):
    def test_fattura(self):
        doc, = leggi_fattura(fattura_xml())
        self.assertEqual(doc['fornitore']['piva'], '01234567890')
        self.assertEqual(doc['fornitore']['denominazione'], 'Detergenti Srl')
        self.assertEqual((doc['tipo'], doc['numero'], doc['data']), ('TD01', '12/A', date(2026, 10, 15)))
        self.assertEqual((doc['imponibile'], doc['iva'], doc['totale']), (Decimal('150.00'), Decimal('33.00'), Decimal('183.00')))
        self.assertEqual([r['imponibile'] for r in doc['righe']], [Decimal('100.00'), Decimal('50.00')])
        self.assertEqual(doc['scadenze'][0]['data'], date(2026, 11, 30))
        self.assertEqual(doc['scadenze'][0]['modalita'], 'MP05')
        self.assertEqual(doc['descrizione'], 'Fornitura ottobre')

    def test_arrotondamento_e_totale_mancante(self):
        doc, = leggi_fattura(fattura_xml(imponibile='150.01', imposta='33.00', totale=''))
        self.assertEqual(doc['righe'][-1]['descrizione'], 'Arrotondamento')
        self.assertEqual(sum(r['imponibile'] for r in doc['righe']), Decimal('150.01'))
        self.assertEqual(doc['totale'], Decimal('183.01'))

    def test_nota_di_credito_negativa(self):
        doc, = leggi_fattura(fattura_xml(tipo='TD04'))
        self.assertEqual((doc['imponibile'], doc['iva'], doc['totale']), (Decimal('-150.00'), Decimal('-33.00'), Decimal('-183.00')))
        self.assertTrue(all(r['imponibile'] < 0 for r in doc['righe']))
        # gia' scritta col segno meno: non si gira due volte
        doc, = leggi_fattura(fattura_xml(tipo='TD04', righe=(('Reso', '-150.00', '22.00'),),
                                         imponibile='-150.00', imposta='-33.00', totale='-183.00'))
        self.assertEqual(doc['imponibile'], Decimal('-150.00'))

    def test_p7m_spezzato_e_base64(self):
        xml = fattura_xml()
        self.assertEqual(xml_da_p7m(p7m(xml)), xml)
        self.assertEqual(xml_da_p7m(base64.encodebytes(p7m(xml))), xml)
        nome, contenuto = estrai_xml('IT01234567890_abc12.xml.p7m', p7m(xml))[0]
        self.assertEqual(nome, 'IT01234567890_abc12.xml')
        self.assertEqual(leggi_fattura(contenuto)[0]['numero'], '12/A')

    def test_zip_ignora_metadati(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('cartella/IT01234567890_aa001.xml', fattura_xml('1'))
            z.writestr('IT01234567890_aa002.xml.p7m', p7m(fattura_xml('2')))
            z.writestr('IT01234567890_aa001_MT_001.xml', b'<metadati/>')
            z.writestr('leggimi.txt', b'ciao')
        trovati = estrai_xml('fatture.zip', buf.getvalue())
        self.assertEqual([n for n, _ in trovati], ['IT01234567890_aa001.xml', 'IT01234567890_aa002.xml'])

    def test_nomi_sdi(self):
        self.assertTrue(e_fattura('IT01234567890_00a1B.xml.p7m'))
        self.assertFalse(e_fattura('IT01234567890_00a1B_MT_001.xml'))
        self.assertFalse(e_fattura('IT01234567890_00a1B_RC_001.xml'))
        self.assertFalse(e_fattura('daticert.xml'))
        self.assertFalse(e_fattura('smime.p7s'))

    def test_non_fattura(self):
        with self.assertRaises(ErroreFattura):
            leggi_fattura(b'<?xml version="1.0"?><RicevutaConsegna/>')
        with self.assertRaises(ErroreFattura):
            leggi_fattura(b'non xml')

    def test_ripartizione(self):
        self.assertEqual(ripartizione_valida({'self': '60', 'servito': '40,0'}),
                         {'self': Decimal('60'), 'servito': Decimal('40.0')})
        self.assertEqual(ripartizione_valida({}), {})
        for sbagliata in ({'self': '60'}, {'self': '120', 'servito': '-20'}, {'boh': '100'}, {'self': 'x'}):
            with self.assertRaises(ValueError):
                ripartizione_valida(sbagliata)


class ImportTest(TestCase):
    def test_import_idempotente_e_fornitore(self):
        Fornitore.objects.create(ragione_sociale='Detergenti (anagrafica)', partita_iva='IT 01234567890')
        esito = importa_file('a.xml', fattura_xml())
        self.assertEqual((len(esito.nuove), len(esito.doppie), esito.errori), (1, 0, []))
        f = esito.nuove[0]
        self.assertEqual(f.fornitore.ragione_sociale, 'Detergenti (anagrafica)')   # trovato per P.IVA
        self.assertEqual((f.competenza_dal, f.competenza_al), (date(2026, 10, 1), date(2026, 10, 1)))
        self.assertEqual(f.righe.count(), 2)
        self.assertEqual(f.scadenze.get().importo, Decimal('183.00'))
        self.assertFalse(f.classificata)
        self.assertEqual(bytes(f.file_xml), fattura_xml())
        esito = importa_file('IT01234567890_x.xml.p7m', p7m(fattura_xml()), 'pec')
        self.assertEqual((len(esito.nuove), len(esito.doppie)), (0, 1))
        self.assertEqual(FatturaPassiva.objects.count(), 1)

    def test_fornitore_nuovo_e_regola(self):
        f = importa_file('a.xml', fattura_xml(piva='09876543210', nome='Energia Spa')).nuove[0]
        self.assertEqual((f.fornitore.ragione_sociale, f.fornitore.partita_iva), ('Energia Spa', '09876543210'))
        energia = VoceConto.objects.get(codice='CV.ENERGIA')
        RegolaFornitore.objects.create(fornitore=f.fornitore, voce=energia, ripartizione={'self': 70, 'servito': 30})
        g = importa_file('b.xml', fattura_xml(numero='13/A', piva='09876543210')).nuove[0]
        self.assertTrue(g.classificata)
        self.assertEqual({r.voce for r in g.righe.all()}, {energia})
        self.assertEqual(g.righe.first().ripartizione_effettiva, {'self': 70, 'servito': 30})

    def test_senza_dati_pagamento_scadenza_alla_data(self):
        f = importa_file('a.xml', fattura_xml(pagamenti=())).nuove[0]
        s = f.scadenze.get()
        self.assertEqual((s.data, s.importo), (date(2026, 10, 15), Decimal('183.00')))

    def test_errore_non_blocca_le_altre(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('IT01234567890_aa001.xml', fattura_xml('1'))
            z.writestr('IT01234567890_aa002.xml', b'<rotto')
        esito = importa_file('lotto.zip', buf.getvalue())
        self.assertEqual(len(esito.nuove), 1)
        self.assertEqual(len(esito.errori), 1)


class PecTest(TestCase):
    def _messaggio_pec(self):
        sdi = EmailMessage()
        sdi['Subject'] = 'Invio File 123456'
        sdi.set_content('Fattura in allegato')
        sdi.add_attachment(p7m(fattura_xml()), maintype='application', subtype='pkcs7-mime',
                           filename='IT01234567890_aa001.xml.p7m')
        sdi.add_attachment(b'<metadati/>', maintype='text', subtype='xml', filename='IT01234567890_aa001_MT_001.xml')
        pec = EmailMessage()
        pec['Subject'] = 'POSTA CERTIFICATA: Invio File 123456'
        pec['Message-ID'] = '<opec123@pec.test>'
        pec['Date'] = 'Thu, 15 Oct 2026 10:00:00 +0200'
        pec.set_content('Messaggio di posta certificata')
        pec.add_attachment(b'<postacert/>', maintype='application', subtype='xml', filename='daticert.xml')
        pec.add_attachment(sdi, filename='postacert.eml')
        return pec.as_bytes()

    def test_fattura_dentro_postacert(self):
        esito = importa_messaggio(self._messaggio_pec())
        self.assertEqual((len(esito.nuove), esito.errori), (1, []))
        self.assertEqual(esito.nuove[0].origine, 'pec')
        m = MessaggioPec.objects.get()
        self.assertEqual((m.message_id, m.fatture), ('<opec123@pec.test>', 1))
        # rilettura: nessun doppione
        esito = importa_messaggio(self._messaggio_pec())
        self.assertEqual((len(esito.nuove), len(esito.doppie)), (0, 1))
        self.assertEqual(MessaggioPec.objects.count(), 1)


def utente(nome, gruppo):
    u = User.objects.create_user(nome, password='x')
    u.groups.add(Group.objects.get_or_create(name=gruppo)[0])
    return u


class VisteTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = utente('titolare1', 'titolare')
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=Group.objects.get(name='titolare'))
        self.client.force_login(self.titolare)

    def apri(self, url):
        """GET con RequestFactory: il client dei test di Django 4.2 non
        rende i template su Python 3.14."""
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from django.urls import resolve
        req = RequestFactory().get(url)
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url.split('?')[0])
        return trovata.func(req, *trovata.args, **trovata.kwargs)

    def test_pagine(self):
        f = importa_file('a.xml', fattura_xml()).nuove[0]
        for url in ('/contabilita/', '/contabilita/?stato=da_classificare', f'/contabilita/fatture/{f.pk}/',
                    '/contabilita/scadenziario/', '/contabilita/piano-dei-conti/'):
            risposta = self.apri(url)
            self.assertEqual(risposta.status_code, 200, url)
        self.assertContains(self.apri('/contabilita/?mese=2026-10'), 'Detergenti Srl')
        risposta = self.apri(f'/contabilita/fatture/{f.pk}/?xml=1')
        self.assertEqual(risposta.content, fattura_xml())

    def test_operatore_senza_permesso(self):
        self.client.force_login(utente('op1', 'operatore'))
        risposta = self.client.get('/contabilita/', HTTP_ACCEPT='application/json')
        self.assertEqual(risposta.status_code, 403)

    def test_carica(self):
        file = SimpleUploadedFile('IT01234567890_aa001.xml.p7m', p7m(fattura_xml()))
        self.client.post('/contabilita/', {'azione': 'carica', 'file': [file]})
        self.assertEqual(FatturaPassiva.objects.get().origine, 'upload')

    def test_classifica_ricorda_e_errori(self):
        f = importa_file('a.xml', fattura_xml()).nuove[0]
        r1, r2 = f.righe.all()
        chimici = VoceConto.objects.get(codice='CV.CHIMICI')
        url = f'/contabilita/fatture/{f.pk}/'
        dati = {'azione': 'salva', f'voce_{r1.pk}': chimici.pk, f'voce_{r2.pk}': chimici.pk,
                f'rip_{r1.pk}_self': '80', f'rip_{r1.pk}_servito': '30',
                'competenza_dal': '2026-10', 'competenza_al': '2026-11', 'ricorda': '1'}
        self.client.post(url, dati)                   # 80 + 30: non salva niente
        f.refresh_from_db()
        self.assertFalse(f.classificata)
        dati[f'rip_{r1.pk}_servito'] = '20'
        self.client.post(url, dati)
        f.refresh_from_db()
        r1.refresh_from_db()
        self.assertTrue(f.classificata)
        self.assertEqual(r1.ripartizione, {'self': 80, 'servito': 20})
        self.assertEqual(f.competenza_al, date(2026, 11, 1))
        regola = RegolaFornitore.objects.get(fornitore=f.fornitore)
        self.assertEqual((regola.voce, regola.ripartizione), (chimici, {'self': 80, 'servito': 20}))

    def test_scadenza_pagata(self):
        f = importa_file('a.xml', fattura_xml()).nuove[0]
        s = f.scadenze.get()
        self.client.post('/contabilita/scadenziario/', {'scadenza': s.pk, 'data': '2026-11-02'})
        s.refresh_from_db()
        self.assertEqual(s.pagata_il, date(2026, 11, 2))

    def test_inserimento_manuale(self):
        voce = VoceConto.objects.get(codice='CF.AFFITTO')
        self.client.post('/contabilita/', {'azione': 'nuova', 'fornitore_nuovo': 'Proprietario', 'numero': '7',
                                           'data': '2026-10-01', 'imponibile': '1.000,00', 'aliquota': '0',
                                           'voce': voce.pk})
        f = FatturaPassiva.objects.get()
        self.assertEqual((f.origine, f.imponibile, f.iva, f.classificata), ('manuale', Decimal('1000.00'), Decimal('0.00'), True))
        self.assertEqual(ScadenzaFattura.objects.get().importo, Decimal('1000.00'))

    def test_voce_nuova(self):
        self.client.post('/contabilita/piano-dei-conti/', {
            'azione': 'salva_voce', 'codice': 'cf.lavanderia', 'nome': 'Lavanderia panni', 'sezione': 'costi_variabili',
            'flusso': 'operativo', 'rip_servito': '100', 'attiva': '1'})
        v = VoceConto.objects.get(codice='CF.LAVANDERIA')
        self.assertEqual(v.ripartizione, {'servito': 100})
