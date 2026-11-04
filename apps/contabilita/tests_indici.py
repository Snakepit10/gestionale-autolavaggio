"""Test degli indici e dell'indebitamento massimo su un mese inventato,
e dei piani dei finanziamenti."""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from .indici import indici, valore_attuale
from .models import ContoFinanziario, Finanziamento, MovimentoFinanziario, ParametriFinanziari, VoceConto
from .prima_nota import conta_cassa
from .spiegazioni import spiegazione
from .tests_analitica import fattura

OGGI = date(2026, 11, 3)


def per_nome(r):
    return {i['nome']: i for s in r['sezioni'] for i in s['indici']}


class FinanziamentoTest(TestCase):
    def test_piano_alla_francese(self):
        f = Finanziamento(descrizione='Mutuo', importo=Decimal('10000'), tasso=Decimal('6'), durata_mesi=60,
                          data_prima_rata=date(2026, 1, 31))
        self.assertEqual(f.rata_mensile, Decimal('193.33'))
        piano = f.piano()
        self.assertEqual((len(piano), piano[-1][4], piano[1][0]), (60, Decimal('0'), date(2026, 2, 28)))
        self.assertEqual(sum(r[3] for r in piano), Decimal('10000'))
        self.assertEqual(f.residuo_al(date(2025, 11, 30)), Decimal('0'))       # non ancora erogato
        self.assertEqual(f.residuo_al(date(2026, 1, 15)), Decimal('10000'))
        self.assertEqual(f.residuo_al(date(2026, 1, 31)), piano[0][4])
        capitale, interessi = f.servizio(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(capitale + interessi, sum(r[1] for r in piano[:12]))
        self.assertEqual(f.scadenza, date(2030, 12, 31))

    def test_fido_e_rata_della_banca(self):
        fido = Finanziamento(importo=Decimal('5000'), tasso=Decimal('12'), durata_mesi=0, data_prima_rata=date(2026, 1, 1))
        self.assertEqual(fido.servizio(date(2026, 1, 1), date(2026, 12, 31)), (Decimal('0'), Decimal('600.00')))
        self.assertEqual(fido.residuo_al(date(2026, 6, 1)), Decimal('5000'))
        f = Finanziamento(importo=Decimal('1000'), durata_mesi=10, rata=Decimal('150'), data_prima_rata=date(2026, 1, 1))
        self.assertEqual(len(f.piano()), 7)                                    # 6 x 150 + 100

    def test_valore_attuale(self):
        self.assertEqual(valore_attuale(Decimal('100'), Decimal('0'), 5), Decimal('6000'))
        self.assertEqual(valore_attuale(Decimal('193.33'), Decimal('6'), 5), Decimal('10000'))


class IndiciTest(TestCase):
    def setUp(self):
        from apps.ordini.models import Ordine
        # contabilita' da ottobre: un solo mese chiuso al 3 novembre, riportato a 12
        ContoFinanziario.objects.update(data_saldo_iniziale=date(2026, 10, 1), saldo_iniziale=0)
        ContoFinanziario.objects.filter(nome='BAPS').update(saldo_iniziale=200)
        # servito 100 netti, acqua 20 (variabile), affitto 30 (fisso): EBITDA 50 al mese
        ordine = Ordine.objects.create(totale=Decimal('122'), totale_finale=Decimal('122'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)))
        fattura('1', '20.00', 'CV.ACQUA')
        fattura('2', '30.00', 'CF.AFFITTO')

    def test_senza_debito(self):
        r = indici(2026, 'canali', OGGI)
        self.assertEqual((r['mesi'], r['fattore']), ([date(2026, 10, 1)], Decimal('12')))
        c = r['capacita']
        self.assertEqual((r['conto']['ricavi'], r['conto']['ebitda']), (Decimal('1200.00'), Decimal('600.00')))
        # imposte stimate al 28% di 600, nessun ammortamento: flusso 432, rata massima 432 / 1,25
        self.assertTrue(c['imposte_stimate'])
        self.assertEqual((c['cfads'], c['rata_max'], c['servizio']), (Decimal('432'), Decimal('345.6'), Decimal('0')))
        self.assertEqual(c['nuovo_dscr'], valore_attuale(Decimal('28.8'), Decimal('6'), 5))
        self.assertEqual((c['pfn'], c['pfn_max'], c['spazio_multiplo']), (Decimal('-200'), Decimal('1800'), Decimal('2000')))
        self.assertEqual((c['nuovo'], c['vincolo'], c['massimo']), (c['nuovo_dscr'], 'dscr', c['nuovo']))
        self.assertIsNone(per_nome(r)['Free cash flow']['valore'])              # nessun estratto conto
        i = per_nome(r)
        self.assertEqual(i['EBITDA margin']['valore'], Decimal('50'))
        self.assertEqual(i['Margine di contribuzione']['stato'], 'ok')          # 80%
        self.assertEqual(i['Pareggio operativo']['valore'], Decimal('450'))     # 360 / 0,8
        self.assertEqual(i['PFN / EBITDA']['stato'], 'ok')                      # cassa netta

    def test_con_finanziamento(self):
        Finanziamento.objects.create(descrizione='Prestito', importo=Decimal('1200'), durata_mesi=12,
                                     data_prima_rata=date(2026, 11, 15))
        ParametriFinanziari.objects.create(multiplo_ebitda=Decimal('3'), capex_mantenimento=Decimal('0'))
        r = indici(2026, 'canali', OGGI)
        c = r['capacita']
        self.assertEqual((c['debito'], c['servizio'], c['pfn']), (Decimal('1200'), Decimal('1200'), Decimal('1000')))
        self.assertEqual((c['rata_libera'], c['nuovo'], c['spazio_multiplo']), (Decimal('0'), Decimal('0'), Decimal('800')))
        self.assertEqual(r['dscr'], Decimal('0.36'))
        self.assertFalse(c['oltre'])                       # come stock il debito ci sta, come rate no
        self.assertEqual(per_nome(r)['DSCR']['stato'], 'critico')

    def test_rate_recenti_e_liquidita_per_conto(self):
        fin = VoceConto.objects.get(codice='N.FINANZIAMENTI')
        baps = ContoFinanziario.objects.get(nome='BAPS')
        for giorno, importo in ((date(2026, 6, 15), '-5000'), (date(2026, 10, 20), '-100')):   # la prima e' finita
            MovimentoFinanziario.objects.create(conto=baps, data=giorno, importo=Decimal(importo), voce=fin,
                                                impronta=f'prova|{giorno}')
        ContoFinanziario.objects.filter(nome='MPS').update(attivo=False, saldo_iniziale=999)
        cassa = ContoFinanziario.objects.get(nome='Cassa contanti')
        MovimentoFinanziario.objects.create(conto=cassa, data=date(2026, 10, 6), importo=Decimal('50'),
                                            impronta='prova|incasso', origine='cassa')
        c = indici(2026, 'canali', OGGI)['capacita']
        self.assertEqual((c['servizio'], len(c['rate_movimenti'])), (Decimal('400'), 1))     # 100 x 12 / 3
        self.assertIn('dal 21/07 al 20/10/2026', c['fonte_servizio'])
        self.assertEqual({s['conto'].nome: s['saldo'] for s in c['saldi_conti']},
                         {'BAPS': Decimal('100'), 'Cassa contanti': Decimal('50')})   # MPS disattivato
        self.assertEqual((c['liquidita'], c['cassa_da_contare']), (Decimal('150'), [cassa]))
        conta_cassa(cassa, date(2026, 10, 31), Decimal('20'))
        c = indici(2026, 'canali', OGGI)['capacita']
        self.assertEqual((c['liquidita'], c['cassa_da_contare']), (Decimal('120'), []))

    def test_ogni_indice_ha_la_sua_spiegazione(self):
        mancanti = [nome for nome in per_nome(indici(2026, 'canali', OGGI)) if not spiegazione(nome)]
        self.assertEqual(mancanti, [])

    def test_casella_di_informazioni(self):
        from .templatetags.contabilita_info import info
        self.assertEqual(info('Voce senza spiegazione'), '')
        testo = str(info('Cicli portali'))
        self.assertIn('data-info-titolo="Cicli portali"', testo)
        self.assertIn('&lt;b&gt;Come si calcola&lt;/b&gt;', testo)            # l'HTML resta dentro l'attributo
        self.assertEqual(testo.count('"'), 6)                                 # solo gli apici dei 3 attributi

    def test_anno_senza_mesi(self):
        self.assertIsNone(indici(2025, 'canali', OGGI))


class VisteIndiciTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)

    def apri(self, url):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from django.urls import resolve
        req = RequestFactory().get(url)
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url.split('?')[0])
        return trovata.func(req, *trovata.args, **trovata.kwargs)

    def test_pagine(self):
        self.client.post('/contabilita/finanziamenti/', {'azione': 'salva', 'descrizione': 'Mutuo prova', 'tipo': 'mutuo',
                                                         'importo': '10.000,00', 'tasso': '6', 'durata_mesi': '60',
                                                         'data_prima_rata': '2026-01-31'})
        f = Finanziamento.objects.get()
        self.assertEqual((f.importo, f.rata), (Decimal('10000.00'), None))
        self.client.post('/contabilita/finanziamenti/', {'azione': 'salva', 'descrizione': 'Errato', 'tipo': 'mutuo',
                                                         'importo': '-5', 'durata_mesi': '10', 'data_prima_rata': '2026-01-31'})
        self.assertEqual(Finanziamento.objects.count(), 1)
        self.assertContains(self.apri('/contabilita/finanziamenti/'), 'Mutuo prova')
        for url in ('/contabilita/indici/', '/contabilita/indici/?anno=2026&ricavi=canali', '/contabilita/indici/?anno=2020',
                    '/contabilita/indici/?anno=xyz'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/indici/?anno=2026'), 'Nuovo finanziamento ottenibile')
        for url in ('/contabilita/indici/?anno=2026', '/contabilita/analitica/?anno=2026',
                    '/contabilita/conto-economico/?anno=2026', '/contabilita/conti/'):
            self.assertContains(self.apri(url), 'info-kpi')
        self.client.post('/contabilita/indici/', {'multiplo_ebitda': '3,5', 'dscr_minimo': '1,3', 'tasso_nuovo_debito': '5',
                                                  'durata_nuovo_debito': '7', 'capex_mantenimento': '40',
                                                  'aliquota_imposte': '24', 'multiplo_valutazione': '4,5'})
        p = ParametriFinanziari.attuali()
        self.assertEqual((p.multiplo_ebitda, p.durata_nuovo_debito, p.multiplo_valutazione), (Decimal('3.5'), 7, Decimal('4.5')))
        self.client.post('/contabilita/indici/', {'multiplo_ebitda': '50', 'dscr_minimo': '1,3', 'tasso_nuovo_debito': '5',
                                                  'durata_nuovo_debito': '7', 'capex_mantenimento': '40',
                                                  'aliquota_imposte': '24', 'multiplo_valutazione': '4,5'})
        self.assertEqual(ParametriFinanziari.attuali().multiplo_ebitda, Decimal('3.5'))
