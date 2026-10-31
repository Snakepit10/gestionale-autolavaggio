"""Test della prima nota: lettura degli estratti (xlsx con e senza
intestazione, CSV dare/avere, xls in HTML), regole, abbinamento dei
pagamenti alle fatture, giroconti, cassa contanti e pagine."""
import io
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .estratti import importa_estratto, movimenti_da_righe, righe_file
from .importa import importa_file
from .models import ContoFinanziario, MovimentoFinanziario, RegolaMovimento, ScadenzaFattura, VoceConto
from .prima_nota import abbina, genera_cassa, giroconto, proponi_abbinamenti, togli_abbinamento
from .tests import fattura_xml


def xlsx(righe):
    import openpyxl
    libro = openpyxl.Workbook()
    for r in righe:
        libro.active.append(r)
    buf = io.BytesIO()
    libro.save(buf)
    return buf.getvalue()


def movimento(conto, importo, descrizione='', data=date(2026, 11, 20)):
    return MovimentoFinanziario.objects.create(conto=conto, data=data, importo=Decimal(importo),
                                               descrizione=descrizione, impronta=f'{importo}|{descrizione}|{data}')


class EstrattiTest(TestCase):
    def setUp(self):
        self.banca = ContoFinanziario.objects.get(nome='MPS')

    def test_intestazione_mps_idempotente_e_regole(self):
        dati = xlsx([
            ['DATA CONT.', 'DATA VAL.', 'CAUSALE', 'DESCRIZIONE', '', 'IMPORTO(EUR)'],
            [datetime(2026, 2, 20), datetime(2026, 2, 20), 'Incasso tramite POS', 'Incasso tramite p.o.s. terminale prova', ' ', 25],
            [datetime(2026, 2, 20), datetime(2026, 2, 20), 'Incasso tramite POS', 'Incasso tramite p.o.s. terminale prova', ' ', 25],
            [datetime(2026, 2, 19), datetime(2026, 2, 1), 'Canone mensile', 'Canone conto di prova', ' ', -7],
            [datetime(2026, 2, 18), datetime(2026, 2, 18), 'Bonifico', 'Bonifico a favore di Ditta Prova', ' ', '-1.250,50'],
            ['Saldo finale', None, None, None, None, 999],
        ])
        esito = importa_estratto(self.banca, 'mps.xlsx', dati)
        self.assertEqual((esito.nuovi, esito.doppi, esito.classificati, esito.errori), (4, 0, 3, []))
        self.assertEqual((esito.dal, esito.al), (date(2026, 2, 18), date(2026, 2, 20)))
        pos = self.banca.movimenti.filter(importo=25)
        self.assertEqual(pos.count(), 2)                       # due POS uguali restano due
        self.assertEqual({m.voce.codice for m in pos}, {'N.INCASSI'})
        self.assertEqual(self.banca.movimenti.get(importo=-7).voce.codice, 'CF.BANCA')
        bonifico = self.banca.movimenti.get(importo=Decimal('-1250.50'))
        self.assertIsNone(bonifico.voce)
        self.assertEqual(bonifico.causale, 'Bonifico')
        esito = importa_estratto(self.banca, 'mps.xlsx', dati)
        self.assertEqual((esito.nuovi, esito.doppi), (0, 4))

    def test_baps_senza_intestazione_e_saldo(self):
        dati = xlsx([
            ['12/02/2026', '11/02/2026', 'EUR', '-1,25', 'PAGAMENTO POS\nNegozio di prova', '998,75'],
            ['12/02/2026', '12/02/2026', 'EUR', '-48,75', 'ADDEBITO SDD\nUtenza di prova', '950,00'],
        ])
        self.banca.saldo_iniziale, self.banca.data_saldo_iniziale = Decimal('1000'), date(2026, 1, 1)
        self.banca.save()
        self.assertEqual(importa_estratto(self.banca, 'baps.xlsx', dati).nuovi, 2)
        m = self.banca.movimenti.get(importo=Decimal('-48.75'))
        self.assertEqual((m.data, m.data_valuta, m.saldo_estratto), (date(2026, 2, 12), date(2026, 2, 12), Decimal('950.00')))
        self.assertEqual(m.descrizione, 'ADDEBITO SDD Utenza di prova')
        self.assertEqual(self.banca.saldo_al(date(2026, 2, 12)), Decimal('950.00'))

    def test_csv_dare_avere_e_html(self):
        testo = ('Data operazione;Valuta;Descrizione;Dare;Avere\n'
                 '02/03/2026;02/03/2026;Versamento contanti;;500,00\n'
                 '03/03/2026;03/03/2026;F24 delega;150,00;\n')
        self.assertEqual(importa_estratto(self.banca, 'estratto.csv', testo.encode('cp1252')).nuovi, 2)
        self.assertEqual(self.banca.movimenti.get(importo=500).voce.codice, 'N.GIROCONTO')
        self.assertTrue(self.banca.movimenti.filter(importo=-150).exists())
        html = ('<html><table><tr><th>Data movimento</th><th>Data valuta</th><th>Descrizione operazione</th><th>Causale</th>'
                '<th>Importo</th></tr><tr><td>10/02/2026</td><td>09/02/2026</td><td>"Pagamento di prova"</td>'
                '<td>PAGAMENTO POS</td><td>-9.99</td></tr></table></html>')
        m, = movimenti_da_righe(righe_file('baps.xls', html.encode()))
        self.assertEqual((m['importo'], m['causale'], m['descrizione']),
                         (Decimal('-9.99'), 'PAGAMENTO POS', 'Pagamento di prova'))

    def test_xls_vecchio_e_file_illeggibile(self):
        self.assertIn('salvalo come .xlsx', importa_estratto(self.banca, 'a.xls', b'\xd0\xcf\x11\xe0' + b'\0' * 20).errori[0])
        self.assertTrue(importa_estratto(self.banca, 'a.csv', b'niente;da;leggere\n').errori)


class PrimaNotaTest(TestCase):
    def setUp(self):
        self.banca = ContoFinanziario.objects.get(nome='BAPS')
        self.cassa = ContoFinanziario.objects.get(nome='Cassa contanti')

    def test_proposta_e_abbinamento(self):
        importa_file('a.xml', fattura_xml())                       # scade il 30/11 per 183
        importa_file('b.xml', fattura_xml(numero='9', piva='09876543210', nome='Altra Ditta'))   # stesso importo
        giusto = movimento(self.banca, '-183.00', 'BONIFICO A FAVORE DI DETERGENTI SRL')
        movimento(self.banca, '-183.00', 'altro', data=date(2027, 3, 1))     # troppo lontano
        proposte = proponi_abbinamenti()
        self.assertEqual(len(proposte), 1)
        pr = proposte[0]
        self.assertEqual((pr['movimento'], pr['scadenza'].fattura.fornitore.ragione_sociale, pr['nome']),
                         (giusto, 'Detergenti Srl', True))
        abbina(giusto, [pr['scadenza']])
        s = ScadenzaFattura.objects.get(pk=pr['scadenza'].pk)
        giusto.refresh_from_db()
        self.assertEqual((s.pagata_il, giusto.voce.codice), (date(2026, 11, 20), 'N.FORNITORI'))
        self.assertEqual(proponi_abbinamenti(), [])
        togli_abbinamento(s)
        giusto.refresh_from_db()
        self.assertIsNone(giusto.voce)

    def test_giroconto(self):
        versamento = movimento(self.cassa, '-500', 'Versamento in banca')
        opposto = giroconto(versamento, self.banca)
        versamento.refresh_from_db()
        self.assertEqual((opposto.conto, opposto.importo, opposto.voce.codice), (self.banca, Decimal('500'), 'N.GIROCONTO'))
        self.assertEqual((versamento.controparte, versamento.voce.codice), (opposto, 'N.GIROCONTO'))

    def test_cassa_dalle_quadrature(self):
        from apps.finanze.models import QuadraturaGiornaliera, SpesaCassa, TicketResto
        giorno = date(2026, 10, 6)
        ottobre = (date(2026, 10, 1), date(2026, 10, 31))
        QuadraturaGiornaliera.objects.create(data=giorno, contanti_totali=Decimal('300'), fondo_cassa_iniziale=Decimal('100'))
        spesa = SpesaCassa.objects.create(data=giorno, importo=Decimal('20'), descrizione='Gasolio', categoria='carburante')
        TicketResto.objects.create(data=giorno, importo=Decimal('5'))
        self.assertEqual(genera_cassa(self.cassa, *ottobre), 3)
        incasso = self.cassa.movimenti.get(descrizione__startswith='Incassi')
        uscita = self.cassa.movimenti.get(descrizione__startswith='Spesa')
        self.assertEqual(incasso.importo, Decimal('225'))                   # 300 - 100 + 20 + 5
        self.assertEqual((uscita.importo, uscita.voce.codice), (Decimal('-20'), 'CF.CARBURANTE'))
        self.assertEqual(self.cassa.saldo_al(giorno), Decimal('200'))
        # rigenerare non duplica; la voce cambiata a mano resta, l'importo si aggiorna
        altri = VoceConto.objects.get(codice='CF.ALTRI')
        uscita.voce = altri
        uscita.save()
        spesa.importo = Decimal('30')
        spesa.save()
        self.assertEqual(genera_cassa(self.cassa, *ottobre), 2)
        self.assertEqual(self.cassa.movimenti.count(), 3)
        uscita.refresh_from_db()
        self.assertEqual((uscita.importo, uscita.voce), (Decimal('-30'), altri))
        spesa.delete()
        genera_cassa(self.cassa, *ottobre)
        self.assertEqual(self.cassa.movimenti.count(), 2)


class VistePrimaNotaTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)
        self.banca = ContoFinanziario.objects.get(nome='BAPS')

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
        m = MovimentoFinanziario.objects.create(conto=self.banca, data=date(2026, 10, 25), importo=Decimal('-183'),
                                                descrizione='Bonifico Detergenti', impronta='x', saldo_estratto=Decimal('-183'))
        importa_file('a.xml', fattura_xml())
        for url in ('/contabilita/prima-nota/?mese=2026-10', '/contabilita/prima-nota/?stato=da_classificare',
                    f'/contabilita/prima-nota/{m.pk}/', '/contabilita/conti/', '/contabilita/regole/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/prima-nota/?mese=2026-10'), 'Pagamenti da abbinare')
        self.assertContains(self.apri('/contabilita/conti/'), 'quadra al')

    def test_importa_regola_e_classifica(self):
        file = SimpleUploadedFile('e.csv', b'Data;Descrizione;Importo\n02/10/2026;ACQUA SPA bolletta;-80,00\n03/10/2026;Bar;-5\n')
        self.client.post('/contabilita/prima-nota/', {'azione': 'importa', 'conto': self.banca.pk, 'file': file})
        self.assertEqual(self.banca.movimenti.count(), 2)
        acqua = VoceConto.objects.get(codice='CV.ACQUA')
        self.client.post('/contabilita/prima-nota/', {'azione': 'regola', 'testo': 'acqua spa', 'segno': 'uscite', 'voce': acqua.pk})
        self.assertEqual(MovimentoFinanziario.objects.get(importo=-80).voce, acqua)
        self.assertTrue(RegolaMovimento.objects.filter(testo='acqua spa').exists())
        bar = MovimentoFinanziario.objects.get(importo=-5)
        altri = VoceConto.objects.get(codice='CF.ALTRI')
        self.client.post('/contabilita/prima-nota/', {'azione': 'classifica', 'mov': [bar.pk], f'voce_{bar.pk}': altri.pk})
        bar.refresh_from_db()
        self.assertEqual(bar.voce, altri)

    def test_saldo_iniziale_e_abbinamento_multiplo(self):
        self.client.post('/contabilita/conti/', {'azione': 'salva_conto', 'id': self.banca.pk, 'nome': 'BAPS', 'tipo': 'banca',
                                                 'saldo_iniziale': '1.234,56', 'data_saldo_iniziale': '2026-01-01', 'attivo': '1'})
        self.banca.refresh_from_db()
        self.assertEqual(self.banca.saldo_iniziale, Decimal('1234.56'))
        a = importa_file('a.xml', fattura_xml()).nuove[0].scadenze.get()
        b = importa_file('b.xml', fattura_xml(numero='13/A')).nuove[0].scadenze.get()
        bonifico = movimento(self.banca, '-366.00', 'Bonifico cumulativo')
        url = f'/contabilita/prima-nota/{bonifico.pk}/'
        self.client.post(url, {'azione': 'abbina', 'scadenza': [a.pk]})          # 183 != 366: niente
        self.assertFalse(ScadenzaFattura.objects.filter(movimento=bonifico).exists())
        self.client.post(url, {'azione': 'abbina', 'scadenza': [a.pk, b.pk]})
        self.assertEqual(ScadenzaFattura.objects.filter(movimento=bonifico).count(), 2)
