"""Test della prima nota: lettura degli estratti (xlsx con e senza
intestazione, CSV dare/avere, xls in HTML), regole, abbinamento dei
pagamenti alle fatture, giroconti, cassa contanti e pagine."""
import io
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .estratti import PREDEFINITI, importa_estratto, movimenti_da_righe, righe_file
from .importa import importa_file
from .models import ContoFinanziario, MovimentoFinanziario, RegolaMovimento, ScadenzaFattura, VoceConto
from .prima_nota import (abbina, conta_cassa, correggi_baps_su_mps, genera_cassa, giroconto, movimenti_di_baps_su_mps,
                         nome_nel_testo, proponi_abbinamenti, togli_abbinamento)
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


def _titolare():
    from apps.auth_system.models import PermessoGruppo
    utente = User.objects.create_user('titolare2', password='x')
    gruppo = Group.objects.get_or_create(name='titolare')[0]
    utente.groups.add(gruppo)
    PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
    return utente


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

    def test_file_di_un_altra_banca_non_entra(self):
        baps = xlsx([['12/02/2026', '11/02/2026', 'EUR', '-1,25', 'PAGAMENTO POS\nNegozio di prova', '998,75']])
        esito = importa_estratto(self.banca, 'baps.xlsx', baps)
        self.assertEqual((esito.nuovi, self.banca.movimenti.count()), (0, 0))
        self.assertIn('formato di BAPS', esito.errori[0])
        # scelto il modello a mano si puo'
        self.assertEqual(importa_estratto(self.banca, 'baps.xlsx', baps, PREDEFINITI['baps_xlsx']).nuovi, 1)

    def test_movimenti_gia_su_un_altro_conto(self):
        altro = ContoFinanziario.objects.get(nome='BAPS')
        righe = [[f'{g:02d}/02/2026', f'{g:02d}/02/2026', 'Bonifico', f'Prova {g}', ' ', -10 - g] for g in range(1, 7)]
        for g in range(1, 7):
            movimento(altro, f'-{10 + g}', f'Stesso movimento {g}', data=date(2026, 2, g))
        esito = importa_estratto(self.banca, 'mps.xlsx', xlsx(righe))
        self.assertEqual(esito.nuovi, 0)
        self.assertIn('6 dei 6 movimenti del file sono gia\' sul conto BAPS', esito.errori[0])

    def test_il_primo_file_fissa_la_banca_del_conto(self):
        nuovo = ContoFinanziario.objects.create(nome='Conto nuovo', data_saldo_iniziale=date(2026, 1, 1))
        baps = xlsx([['12/02/2026', '11/02/2026', 'EUR', '-1,25', 'Pagamento di prova', '998,75']])
        self.assertEqual(importa_estratto(nuovo, 'baps.xlsx', baps).nuovi, 1)
        nuovo.refresh_from_db()
        self.assertEqual(nuovo.banca_estratto, 'BAPS')

    def test_baps_senza_intestazione_e_saldo(self):
        self.banca = ContoFinanziario.objects.get(nome='BAPS')
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
        self.assertIn('non si apre', importa_estratto(self.banca, 'a.xls', b'\xd0\xcf\x11\xe0' + b'\0' * 20).errori[0])
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

    def test_proposte_col_nome_del_fornitore(self):
        chimici = VoceConto.objects.get(codice='CV.CHIMICI')
        banca = VoceConto.objects.get(codice='CF.BANCA')
        for numero in ('1', '2'):                                  # 183 l'una, scadenza 30/11
            importa_file('a.xml', fattura_xml(numero=numero))
        importa_file('b.xml', fattura_xml(numero='3', piva='09876543210', nome='Altra Ditta', totale='100.00',
                                          pagamenti=(('2026-11-30', '100.00'),)))
        # due fatture con un bonifico, lontano e gia' sul costo: conterebbe due volte
        insieme = movimento(self.banca, '-367.50', 'BONIFICO A FAVORE DI DETERGENTI SRL', data=date(2026, 7, 10))
        MovimentoFinanziario.objects.filter(pk=insieme.pk).update(voce=chimici)
        # la commissione del bonifico ha il nome ma non e' un pagamento; senza nome non si tocca un costo
        spese = movimento(self.banca, '-366', 'COMMISSIONI DETERGENTI SRL', data=date(2026, 7, 11))
        senza_nome = movimento(self.banca, '-100.00', 'ADDEBITO VARIO', data=date(2026, 7, 12))
        MovimentoFinanziario.objects.filter(pk=spese.pk).update(voce=banca)
        MovimentoFinanziario.objects.filter(pk=senza_nome.pk).update(voce=chimici)

        [p] = proponi_abbinamenti()
        self.assertEqual((p['movimento'], len(p['scadenze']), p['commissione'], p['era_costo']),
                         (insieme, 2, Decimal('1.50'), chimici))
        self.client.force_login(_titolare())
        self.client.post('/contabilita/prima-nota/', {'azione': 'abbina_tutte'})
        insieme.refresh_from_db()
        self.assertEqual((insieme.voce.codice, insieme.scadenze.count(), insieme.differenza),
                         ('N.FORNITORI', 2, Decimal('-1.50')))             # il costo resta solo in fattura

    def test_nomi_corti_dei_fornitori(self):
        self.assertTrue(nome_nel_testo('ABM Group S.r.l.', 'BONIFICO A ABM GROUP'))
        self.assertTrue(nome_nel_testo('KSM S.P.A.', 'Bonifico a favore di ksm'))
        self.assertFalse(nome_nel_testo('KSM S.P.A.', 'Pagamento KSMART'))
        self.assertFalse(nome_nel_testo('ABM S.P.A.', 'Bonifico spa di prova'))

    def test_macchinario_pagato_resta_investimento(self):
        investimenti = VoceConto.objects.get(codice='N.INVESTIMENTI')
        fattura = importa_file('a.xml', fattura_xml(numero='1')).nuove[0]
        fattura.righe.update(voce=investimenti)
        assegno = movimento(self.banca, '-83.00', 'Proprio assegno', data=date(2026, 11, 2))
        resto = abbina(assegno, list(fattura.scadenze.all()), parziale=True)
        assegno.refresh_from_db()
        self.assertEqual((assegno.voce, resto.importo), (investimenti, Decimal('100.00')))
        contanti = movimento(self.cassa, '-100.00', 'Saldo in contanti', data=date(2026, 11, 3))
        abbina(contanti, [resto])
        contanti.refresh_from_db()
        self.assertEqual(contanti.voce, investimenti)

    def test_scadenziario_pagate_in_gruppo(self):
        importa_file('a.xml', fattura_xml(numero='1'))
        importa_file('b.xml', fattura_xml(numero='2'))
        importa_file('c.xml', fattura_xml(numero='3'))
        s1, s2, s3 = ScadenzaFattura.objects.order_by('pk')
        self.client.force_login(_titolare())
        self.client.post('/contabilita/scadenziario/', {'azione': 'contanti', 'scelte': [s1.pk, s2.pk], 'data': ''})
        uscite = self.cassa.movimenti.order_by('pk')
        self.assertEqual([(m.data, m.importo, m.voce.codice) for m in uscite],
                         [(date(2026, 11, 30), Decimal('-183.00'), 'N.FORNITORI')] * 2)
        s1.refresh_from_db()
        self.assertEqual((s1.movimento, s1.pagata_il), (uscite[0], date(2026, 11, 30)))
        togli_abbinamento(s1)                                       # tolta, l'uscita di cassa sparisce
        self.assertEqual(self.cassa.movimenti.count(), 1)
        self.client.post('/contabilita/scadenziario/', {'azione': 'gia_pagate', 'scelte': [s3.pk], 'data': '2026-01-15'})
        s3.refresh_from_db()
        self.assertEqual((s3.pagata_il, s3.movimento), (date(2026, 1, 15), None))

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
        incasso = self.cassa.movimenti.get(descrizione__startswith='Contanti')
        uscita = self.cassa.movimenti.get(descrizione__startswith='Spesa')
        self.assertEqual(incasso.importo, Decimal('225'))                   # 300 - 100 + 20 + 5
        self.assertEqual(incasso.voce.codice, 'N.INC.ALTRO')                # nessuno scontrino: tutto senza
        self.assertEqual((uscita.importo, uscita.voce.codice), (Decimal('-20'), 'CF.CARBURANTE'))
        resto = self.cassa.movimenti.get(descrizione__startswith='Ticket')
        self.assertEqual((resto.importo, resto.voce.codice), (Decimal('-5'), 'N.TICKET'))   # non sugli incassi POS
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

    def test_cassa_divisa_per_fonte(self):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica, QuadraturaGiornaliera, SpesaCassa
        ottobre = (date(2026, 10, 1), date(2026, 10, 31))
        gettoni = Cassa.objects.create(nome='Gettoni prova', tipo='automatica')
        registratore = Cassa.objects.create(nome='Registratore prova', tipo='automatica', modalita_registratore=True)
        # il 5 non c'e' quadratura: si conta il 6. Casse automatiche: vendita totale (contanti e carte), non l'incasso
        for giorno, contante, carte, incasso in ((5, '45', '5', '70'), (6, '70', '30', '120')):
            ChiusuraCassaAutomatica.objects.create(cassa=gettoni, data=date(2026, 10, giorno), vendita_contante=Decimal(contante),
                                                   vendita_non_contante=Decimal(carte), incasso_totale=Decimal(incasso))
        for giorno, totale in ((5, '40'), (6, '50')):                # registratore: contanti e carte insieme
            ChiusuraCassaAutomatica.objects.create(cassa=registratore, data=date(2026, 10, giorno), incasso_totale=Decimal(totale))
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 4), contanti_totali=Decimal('100'), fondo_cassa_iniziale=Decimal('100'))
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 6), contanti_totali=Decimal('290'), fondo_cassa_iniziale=Decimal('100'),
                                             lettore_carte_servito=Decimal('30'),     # 40 + 50 - 30 = 60 in contanti
                                             pos_casse_automatiche=Decimal('30'))     # 45 + 5 + 70 + 30 - 30 = 120
        SpesaCassa.objects.create(data=date(2026, 10, 6), importo=Decimal('10'), descrizione='Gasolio', categoria='carburante')
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 7), contanti_totali=Decimal('100'), fondo_cassa_iniziale=Decimal('100'))
        genera_cassa(self.cassa, *ottobre)
        parti = {m.voce.codice: m.importo for m in self.cassa.movimenti.filter(data=date(2026, 10, 6))}
        # contati 290 - 100 + 10 di spesa = 200; la spesa resta un'uscita a parte
        self.assertEqual(parti, {'N.INC.SERVITO': Decimal('60'), 'N.INC.SELF': Decimal('120'), 'N.INC.ALTRO': Decimal('20'),
                                 'CF.CARBURANTE': Decimal('-10')})
        self.assertEqual(self.cassa.movimenti.filter(data=date(2026, 10, 7)).count(), 0)   # niente contanti, niente righe
        self.assertEqual(self.cassa.saldo_al(date(2026, 10, 31)), Decimal('190'))         # il totale non cambia
        self.assertEqual(self.cassa.saldo_al(date(2026, 10, 6)), Decimal('190'))
        # flussi di cassa: dalla quadratura reale o dagli scontrini (60 + 120 = 180)
        from .conto_economico import flussi_di_cassa
        reale, scontrini = (flussi_di_cassa([date(2026, 10, 1)], base) for base in ('reale', 'scontrini'))
        voci_reale = {r['nome']: r['totale'] for r in reale['righe'] if r['tipo'] == 'voce'}
        voci_scontrini = {r['nome']: r['totale'] for r in scontrini['righe'] if r['tipo'] == 'voce'}
        self.assertEqual(voci_reale['Incassi in contanti senza scontrino'], Decimal('20'))
        self.assertNotIn('Incassi in contanti senza scontrino', voci_scontrini)
        self.assertEqual((voci_scontrini['Incassi in contanti dal servito'], voci_scontrini['Incassi in contanti dalle casse automatiche']),
                         (Decimal('60'), Decimal('120')))
        finale = lambda fc: next(r['totale'] for r in fc['righe'] if r['chiave'] == 'finale')
        self.assertEqual(finale(reale) - finale(scontrini), Decimal('20'))
        self.assertEqual((reale['senza_scontrino'], scontrini['senza_scontrino']), (Decimal('20'), Decimal('20')))
        cassa = lambda fc: next(c for c in fc['per_conto'] if c['conto'] == self.cassa)['finale']
        self.assertEqual(cassa(reale) - cassa(scontrini), Decimal('20'))

    def test_correggi_baps_caricato_su_mps(self):
        mps = ContoFinanziario.objects.get(nome='MPS')
        spese = VoceConto.objects.get(codice='CF.BANCA')
        giusto = movimento(mps, '-7', 'Bonifico', data=date(2026, 7, 1))
        MovimentoFinanziario.objects.filter(pk=giusto.pk).update(causale='Bonifico')
        doppio = movimento(mps, '-20', 'Commissione doppia', data=date(2026, 7, 2))
        MovimentoFinanziario.objects.filter(pk=doppio.pk).update(voce=spese, ripartizione={'self': 100})
        originale = movimento(self.banca, '-20', 'Commissione doppia', data=date(2026, 7, 2))
        importa_file('a.xml', fattura_xml())                       # scade il 30/11 per 183
        pagata = ScadenzaFattura.objects.get()
        doppio_pagato = movimento(mps, '-183', 'Bonifico a fornitore', data=date(2026, 7, 3))
        abbina(doppio_pagato, [pagata])
        originale2 = movimento(self.banca, '-183', 'Bonifico a fornitore', data=date(2026, 7, 3))
        da_spostare = movimento(mps, '-5', 'Solo qui', data=date(2026, 9, 20))

        r = movimenti_di_baps_su_mps()
        self.assertEqual((r['n'], len(r['doppi']), r['da_spostare'], r['totale']), (3, 2, [da_spostare], Decimal('-208')))
        self.assertEqual(correggi_baps_su_mps(), (2, 1))
        self.assertEqual(list(mps.movimenti.all()), [giusto])
        originale.refresh_from_db()
        originale2.refresh_from_db()
        pagata.refresh_from_db()
        da_spostare.refresh_from_db()
        self.assertEqual((originale.voce, originale.ripartizione), (spese, {'self': 100}))    # il lavoro fatto resta
        self.assertEqual((pagata.movimento, originale2.voce.codice), (originale2, 'N.FORNITORI'))
        self.assertEqual(da_spostare.conto, self.banca)
        self.assertIsNone(movimenti_di_baps_su_mps())

    def test_pos_nei_contanti_e_stipendi_in_contanti(self):
        from apps.personale.models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese
        ottobre = (date(2026, 10, 1), date(2026, 10, 31))
        incassi = VoceConto.objects.get(codice='N.INCASSI')
        mps = ContoFinanziario.objects.get(nome='MPS')
        for giorno, importo, testo in ((5, '40', 'Incasso tramite P.O.S. prova'), (6, '100', 'Bonifico a tuo favore'),
                                       (7, '10', 'DEPOSITO di prova'), (30, '20', 'Accredito Pos prova')):
            m = movimento(mps, importo, testo, data=date(2026, 10, giorno))
            MovimentoFinanziario.objects.filter(pk=m.pk).update(voce=incassi)
        # occasionale: 8 ore a 10 tutte fuori busta; contrattualizzato: pagato 15 scritto a mano
        mese = MesePersonale.objects.create(mese=date(2026, 10, 1))
        for nome, tipo, ore, pagato in (('Prova A', 'occasionale', '8', None), ('Prova B', 'contrattualizzato', '10', '15')):
            d = Dipendente.objects.create(nome=nome, tipo=tipo)
            ParametroRetributivo.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), tariffa_oraria=Decimal('10'),
                                                coefficiente_lordo=Decimal('1.3'))
            RigaMese.objects.create(mese=mese, dipendente=d, busta_paga=Decimal('90') if pagato else None,
                                    pagato=Decimal(pagato) if pagato else None)
            Presenza.objects.create(dipendente=d, data=date(2026, 10, 2), ore=Decimal(ore))

        genera_cassa(self.cassa, *ottobre)                  # senza impostazioni: niente
        self.assertFalse(self.cassa.movimenti.filter(impronta__regex=r'^cassa\|(pos|stipendi)\|').exists())
        self.cassa.pos_nei_contanti_fino_al, self.cassa.stipendi_in_contanti = date(2026, 10, 28), True
        self.cassa.save()
        genera_cassa(self.cassa, *ottobre)
        pos = self.cassa.movimenti.get(impronta='cassa|pos|2026-10')
        self.assertEqual((pos.data, pos.importo, pos.voce), (date(2026, 10, 28), Decimal('-40'), incassi))
        stipendi = self.cassa.movimenti.get(impronta='cassa|stipendi|2026-10')
        self.assertEqual((stipendi.data, stipendi.importo, stipendi.voce.codice),
                         (date(2026, 10, 31), Decimal('-95.00'), 'N.STIPENDI'))        # 80 + 15
        self.cassa.pos_nei_contanti_fino_al, self.cassa.stipendi_in_contanti = None, False
        self.cassa.save()
        genera_cassa(self.cassa, *ottobre)                  # tolte le impostazioni, tolti i movimenti
        self.assertFalse(self.cassa.movimenti.filter(impronta__regex=r'^cassa\|(pos|stipendi)\|').exists())

    def test_versamenti_in_banca_e_conta(self):
        from apps.finanze.models import QuadraturaGiornaliera
        ottobre = (date(2026, 10, 1), date(2026, 10, 31))
        giroconto_ = VoceConto.objects.get(codice='N.GIROCONTO')
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 5), contanti_totali=Decimal('1000'))
        vers = movimento(self.banca, '600', 'VERS. CONT. ATM prova', data=date(2026, 10, 7))
        bonifico = movimento(self.banca, '50', 'BONIFICO DA PROVA', data=date(2026, 10, 7))
        MovimentoFinanziario.objects.filter(pk__in=[vers.pk, bonifico.pk]).update(voce=giroconto_)
        # un giroconto fatto a mano ha gia' il suo opposto: non si ripete
        giroconto(movimento(self.cassa, '-100', 'Versato a mano', data=date(2026, 10, 8)), self.banca)
        self.assertEqual(genera_cassa(self.cassa, *ottobre), 2)                # incasso + versamento da 600
        uscita = self.cassa.movimenti.get(impronta=f'cassa|versamento|{vers.pk}')
        self.assertEqual((uscita.importo, uscita.controparte, uscita.voce), (Decimal('-600'), vers, giroconto_))
        self.assertEqual(self.cassa.saldo_al(date(2026, 10, 31)), Decimal('300'))
        self.assertEqual(genera_cassa(self.cassa, *ottobre), 0)                # rigenerare non duplica

        # contati 120 il 10/10: 180 usciti senza traccia, da classificare
        conta = conta_cassa(self.cassa, date(2026, 10, 10), Decimal('120'))
        self.assertEqual((conta.importo, conta.voce, conta.saldo_estratto), (Decimal('-180'), None, Decimal('120')))
        self.assertEqual(self.cassa.saldo_al(date(2026, 10, 10)), Decimal('120'))
        self.assertEqual(self.cassa.ultima_conta(), conta)
        # un versamento arrivato dopo, prima della conta: la differenza si rifa'
        vers2 = movimento(self.banca, '100', 'Vers. contante self service', data=date(2026, 10, 9))
        MovimentoFinanziario.objects.filter(pk=vers2.pk).update(voce=giroconto_)
        genera_cassa(self.cassa, *ottobre)
        conta.refresh_from_db()
        self.assertEqual((conta.importo, self.cassa.saldo_al(date(2026, 10, 10))), (Decimal('-80'), Decimal('120')))
        self.assertEqual(conta_cassa(self.cassa, date(2026, 10, 10), Decimal('200')).importo, Decimal('0'))


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
