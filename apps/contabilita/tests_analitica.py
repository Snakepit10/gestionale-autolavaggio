"""Test della contabilita' analitica su un mese inventato: ripartizioni
delle voci, delle righe e del personale, direct costing del self-service,
full costing del servito, quadratura col conto economico."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from .analitica import analitica, dividi
from .conto_economico import conto_economico
from .importa import importa_file
from .models import Cespite, RipartizionePersonale, VoceConto
from .tests import fattura_xml

OTTOBRE = date(2026, 10, 1)
ZERO = Decimal('0.00')


def fattura(numero, imponibile, codice, ripartizione_riga=None):
    f = importa_file(f'{numero}.xml', fattura_xml(numero=numero, righe=(('Voce', imponibile, '22.00'),),
                                                    imponibile=imponibile, imposta='0.00', totale=imponibile,
                                                    pagamenti=())).nuove[0]
    f.righe.update(voce=VoceConto.objects.get(codice=codice), ripartizione=ripartizione_riga or {})
    return f


class DividiTest(TestCase):
    def test_arrotondamento(self):
        quote = dividi(Decimal('100'), {'self': 33.33, 'servito': 33.33, 'prodotti': 33.34})
        self.assertEqual(sum(quote.values()), Decimal('100'))
        self.assertEqual(dividi(Decimal('10'), {}), {'comune': Decimal('10')})


class AnaliticaTest(TestCase):
    def setUp(self):
        from apps.core.models import Categoria, ServizioProdotto
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.ordini.models import ItemOrdine, Ordine
        from apps.personale.models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese
        # ricavi: servito 100 netti (un lavaggio a mano, senza portale), portali 200 netti con 20 cicli
        ordine = Ordine.objects.create(totale=Decimal('122'), totale_finale=Decimal('122'))
        self.categoria = Categoria.objects.create(nome='Servito')
        a_mano = ServizioProdotto.objects.create(titolo='Lavaggio a mano', categoria=self.categoria, prezzo=Decimal('122'),
                                                 descrizione='', programmi_portale='')
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=a_mano, quantita=1, prezzo_unitario=a_mano.prezzo)
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)),
                                                   totale=Decimal('122'), totale_finale=Decimal('122'))
        registratore = Cassa.objects.create(nome='Registratore prova', tipo='automatica', modalita_registratore=True)
        ChiusuraCassaAutomatica.objects.create(cassa=registratore, data=date(2026, 10, 6), incasso_totale=Decimal('122'))
        portale = Cassa.objects.create(nome='Portale prova', tipo='automatica', tracking_washcycles=True)
        ChiusuraCassaAutomatica.objects.create(cassa=portale, data=date(2026, 10, 6), vendita_contante=Decimal('244'),
                                               incasso_totale=Decimal('244'), wash_cycles=20)
        # acqua 70/30 dalla voce, chimici tutto self sulla riga, affitto sul comune
        acqua = VoceConto.objects.get(codice='CV.ACQUA')
        acqua.ripartizione = {'self': 70, 'servito': 30}
        acqua.save()
        fattura('1', '100.00', 'CV.ACQUA')
        fattura('2', '50.00', 'CV.CHIMICI', {'self': 100})
        fattura('3', '300.00', 'CF.AFFITTO')
        # personale: 8 ore a 10 euro, 75% servito e 25% self
        d = Dipendente.objects.create(nome='Prova', tipo='occasionale')
        ParametroRetributivo.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), tariffa_oraria=Decimal('10'),
                                            coefficiente_lordo=Decimal('1'))
        RigaMese.objects.create(mese=MesePersonale.objects.create(mese=OTTOBRE), dipendente=d, busta_paga=Decimal('80'))  # tutto in busta
        Presenza.objects.create(dipendente=d, data=date(2026, 10, 2), ore=Decimal('8'))
        RipartizionePersonale.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), ripartizione={'servito': 75, 'self': 25})
        # portale da 1200 in un anno: 100 al mese sul self
        Cespite.objects.create(descrizione='Portale', data_acquisto=date(2026, 10, 1), valore=Decimal('1200'), anni=1, centro='self')

    def test_direct_costing_self(self):
        s = analitica([OTTOBRE], 'scontrini')['self']
        self.assertEqual(s['ricavi'], Decimal('200.00'))
        self.assertEqual(s['variabili'], Decimal('120.00'))          # acqua 70 + chimici 50
        self.assertEqual(s['mdc1'], Decimal('80.00'))
        self.assertEqual(s['fissi_specifici'], Decimal('120.00'))    # personale 20 + ammortamento 100
        self.assertEqual(s['mdc2'], Decimal('-40.00'))
        self.assertEqual((s['cicli'], s['ricavo_ciclo'], s['variabile_ciclo']), (20, Decimal('10'), Decimal('6')))
        self.assertEqual(s['cicli_pareggio'], Decimal('30'))         # 120 / (80 / 20)

    def aggiungi_portale(self, quantita, chiuso_senza_portale=0):
        """Servizi del servito con programmi portale: un ciclo per pezzo."""
        from apps.core.models import ServizioProdotto
        from apps.finanze.models import LavaggioSenzaPortale
        from apps.ordini.models import ItemOrdine, Ordine
        completo = ServizioProdotto.objects.create(titolo='Lavaggio Completo - Media', categoria=self.categoria,
                                                   prezzo=Decimal('0'), descrizione='', programmi_portale='1,2')
        ordine = Ordine.objects.get()
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=completo, quantita=quantita, prezzo_unitario=0)
        for _ in range(chiuso_senza_portale):
            item = ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=completo, quantita=1, prezzo_unitario=0)
            LavaggioSenzaPortale.objects.create(item=item)
        Ordine.objects.filter(pk=ordine.pk).update(totale=Decimal('122'), totale_finale=Decimal('122'))

    def test_cicli_self_tolgono_il_servito(self):
        """I servizi con programmi portale passano dal portale: non sono cicli self.
        Quelli chiusi senza portale non contano."""
        self.aggiungi_portale(4, chiuso_senza_portale=1)
        s = analitica([OTTOBRE], 'scontrini')['self']
        self.assertEqual((s['cicli_totali'], s['cicli_servito'], s['cicli']), (20, 4, 16))
        self.assertEqual(s['ricavo_ciclo'], Decimal('12.5'))         # 200 / 16

    def test_lavaggi_senza_vendite_di_abbonamenti(self):
        """La vendita di un abbonamento (ordine senza righe) non e' un lavaggio;
        l'accesso in abbonamento (servizio a 0 euro) si'."""
        from apps.core.models import ServizioProdotto
        from apps.ordini.models import ItemOrdine, Ordine
        quando = timezone.make_aware(datetime(2026, 10, 7, 9, 0))
        vendita = Ordine.objects.create(totale=Decimal('50'), totale_finale=Decimal('50'), origine='abbonamento')
        accesso = Ordine.objects.create(totale=0, totale_finale=0, origine='abbonamento')
        ItemOrdine.objects.create(ordine=accesso, servizio_prodotto=ServizioProdotto.objects.get(titolo='Lavaggio a mano'),
                                  quantita=1, prezzo_unitario=0)
        Ordine.objects.filter(pk__in=(vendita.pk, accesso.pk)).update(data_ora=quando)
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual((s['lavaggi'], s['lavaggi_abbonamento']), (2, 1))

    def test_full_costing_servito(self):
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual(s['ricavi'], Decimal('100.00'))
        self.assertEqual(s['diretti'], Decimal('90.00'))             # acqua 30 + personale 60
        self.assertEqual(s['comuni'], Decimal('300.00'))
        self.assertEqual(s['quota_comuni'], Decimal('100.00'))       # affitto per ricavi: 100 su 300
        self.assertEqual(s['risultato'], Decimal('-90.00'))
        self.assertEqual((s['lavaggi'], s['costo_lavaggio']), (1, Decimal('190.00')))
        self.assertEqual(s['ore'], Decimal('6'))

    def test_driver_dei_costi_comuni(self):
        affitto = VoceConto.objects.get(codice='CF.AFFITTO')
        # ore: 2 self, 6 servito
        affitto.driver_comuni = 'ore'
        affitto.save()
        a = analitica([OTTOBRE], 'scontrini')
        self.assertEqual((a['self']['quota_comuni'], a['servito']['quota_comuni']), (Decimal('75.00'), Decimal('225.00')))
        # cicli: nessun ciclo del servito, tutto al self
        affitto.driver_comuni = 'cicli'
        affitto.save()
        a = analitica([OTTOBRE], 'scontrini')
        self.assertEqual((a['self']['quota_comuni'], a['servito']['quota_comuni']), (Decimal('300.00'), ZERO))
        self.assertEqual(a['self']['costo_ciclo_portale'], Decimal('15'))   # 300 / 20 cicli
        # 16 cicli self e 4 del servito: il servito paga 4 cicli a 15 euro
        self.aggiungi_portale(4)
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual((s['quota_comuni'], s['cicli_portale'], s['costo_cicli_portale']),
                         (Decimal('60.00'), 4, Decimal('60.00')))

    def test_quadra_col_conto_economico(self):
        a = analitica([OTTOBRE], 'scontrini')
        ebit = next(r['totale'] for r in conto_economico([OTTOBRE], 'scontrini')['righe'] if r['chiave'] == 'ebit')
        self.assertEqual(a['quadro'][-1]['totale'], ebit)
        self.assertEqual(ebit, Decimal('-330.00'))
        # risultati pieni: self -240, servito -90, prodotti 0; il Comune va a zero
        self.assertEqual(a['quadro'][-1]['nome'], 'Risultato pieno')
        self.assertEqual(a['quadro'][-1]['valori'], [Decimal('-240.00'), Decimal('-90.00'), ZERO, ZERO])
        self.assertEqual(a['self']['risultato'], Decimal('-240.00'))

    def test_rientro_investimenti(self):
        from .models import ContoFinanziario, MovimentoFinanziario
        MovimentoFinanziario.objects.create(conto=ContoFinanziario.objects.get(nome='BAPS'), data=date(2026, 10, 15),
                                            importo=Decimal('-50'), voce=VoceConto.objects.get(codice='N.FINANZIAMENTI'),
                                            impronta='rata')
        r = analitica([OTTOBRE], 'scontrini')['rientro']
        self_ = next(x for x in r['righe'] if x['nome'] == 'Self-service')
        # self: ricavi 200 - costi 240 + ammortamento 100 = 60 al mese -> 720 all'anno su 1200 investiti
        self.assertEqual((self_['investito'], self_['ante'], self_['anni_ante']), (Decimal('1200'), Decimal('720'), Decimal('1.7')))
        self.assertEqual(self_['rientro_ante'].year, 2028)
        # rata 50 al mese -> 600 all'anno, tutta sul self (unico centro con cespiti)
        self.assertEqual((self_['dopo'], self_['anni_dopo']), (Decimal('120'), Decimal('10.0')))
        # in totale l'EBITDA (-230 al mese) e' negativo: non rientra
        self.assertEqual((r['totale']['ante'], r['totale']['anni_ante']), (Decimal('-2760'), None))
        Cespite.objects.all().delete()
        self.assertIsNone(analitica([OTTOBRE], 'scontrini')['rientro'])

    def test_ricavi_dal_rilevato(self):
        from apps.finanze.models import QuadraturaGiornaliera
        # rilevato 366: il servito e' il totale ordini (122) meno l'IVA del registratore (22);
        # i portali sono il resto (366 - 122 = 244) meno l'IVA dei corrispettivi del portale (44)
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 6), contanti_totali=Decimal('366'))
        a = analitica([OTTOBRE], 'rilevato')
        self.assertEqual(a['servito']['ricavi'], Decimal('100.00'))
        self.assertEqual(a['self']['ricavi'], Decimal('200.00'))
        self.assertEqual(a['self']['ricavo_ciclo'], Decimal('10'))
        self.assertEqual(a['quadro'][0]['totale'], Decimal('300.00'))
        ebit = next(r['totale'] for r in conto_economico([OTTOBRE], 'rilevato')['righe'] if r['chiave'] == 'ebit')
        self.assertEqual(a['quadro'][-1]['totale'], ebit)
        self.assertEqual(analitica([OTTOBRE], 'scontrini')['self']['ricavi'], Decimal('200.00'))   # scontrini: invariato


class VisteAnaliticaTest(TestCase):
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

    def test_pagine_e_ripartizione_personale(self):
        from apps.personale.models import Dipendente
        d = Dipendente.objects.create(nome='Prova')
        for url in ('/contabilita/analitica/?anno=2026', '/contabilita/analitica/?anno=2026&mese=10',
                    '/contabilita/analitica/?anno=2026&ricavi=xyz',
                    '/contabilita/analitica/?anno=2026&ricavi=scontrini',
                    '/contabilita/analitica/impostazioni/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'senza ripartizione')
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'Nessun cespite registrato')
        Cespite.objects.create(descrizione='Portale', data_acquisto=date(2026, 1, 10), valore=Decimal('1200'), anni=5, centro='self')
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'acquisto medio 01/2026')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'personale', 'dal': '2026-10',
                                                                  f'rip_{d.pk}_servito': '80', f'rip_{d.pk}_self': '20'})
        r = RipartizionePersonale.objects.get()
        self.assertEqual((r.valido_dal, r.ripartizione), (OTTOBRE, {'servito': 80, 'self': 20}))
        affitto = VoceConto.objects.get(codice='CF.AFFITTO')
        self.assertContains(self.apri('/contabilita/analitica/impostazioni/'), f'drv_v_{affitto.pk}')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'driver', f'drv_v_{affitto.pk}': 'cicli',
                                                                  f'drv_c_{Cespite.objects.get().pk}': 'ore'})
        affitto.refresh_from_db()
        self.assertEqual(affitto.driver_comuni, 'cicli')
        self.assertEqual(Cespite.objects.get().driver_comuni, 'ricavi')      # cespite del self: non si tocca


class ProduttivitaTest(TestCase):
    """Stesso mese di prova di AnaliticaTest: un lavaggio il 6/10 (martedi'),
    8 ore di presenza il 2/10 al 75% servito."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        AnaliticaTest.setUp(self)

    def test_kpi_del_mese(self):
        from .produttivita import derivati, kpi_mese
        k = kpi_mese(OTTOBRE, 'scontrini')
        self.assertEqual((k['lavaggi'], k['giorni_aperti']), (1, 1))
        self.assertEqual((k['ore'], k['giorni_persona']), (Decimal('6'), Decimal('0.75')))
        self.assertEqual((k['ricavi'], k['personale'], k['variabili'], k['pieno']),
                         (Decimal('100.00'), Decimal('60.00'), Decimal('30.00'), Decimal('190.00')))
        d = derivati(k)
        self.assertEqual(d['persone_giorno'], Decimal('0.75'))
        self.assertEqual(d['lavoro_ricavi'], Decimal('60'))
        self.assertEqual((d['consumi_lavaggio'], d['costo_lavaggio'], d['risultato_lavaggio']),
                         (Decimal('30'), Decimal('190'), Decimal('-90')))
        self.assertEqual(round(d['auto_ora'], 4), Decimal('0.1667'))
        self.assertIsNone(kpi_mese(date(2099, 1, 1), 'scontrini'))

    def test_andamento_e_anno_prima(self):
        from .produttivita import andamento
        t = andamento(2026, 'scontrini')
        lavaggi = next(r for r in t['righe'] if r['chiave'] == 'lavaggi')
        self.assertEqual(lavaggi['celle'][9]['v'], 1)          # ottobre
        self.assertIsNone(lavaggi['celle'][9]['var'])         # nel 2025 nessun dato
        self.assertEqual(lavaggi['celle'][12]['v'], 1)         # totale
        self.assertEqual(len(t['mesi']), 13)

    def test_giorni_settimana_meteo_e_mix(self):
        from unittest import mock

        from apps.core.models import ServizioProdotto
        from apps.ordini.models import ItemOrdine, Ordine

        from .produttivita import giorni_del_periodo, mix_ed_extra, per_giorno_settimana, per_meteo
        fine = date(2026, 10, 31)
        giorni = giorni_del_periodo(OTTOBRE, fine)
        self.assertEqual([(g['data'], g['lavaggi'], g['ricavo']) for g in giorni], [(date(2026, 10, 6), 1, Decimal('100'))])
        settimana = per_giorno_settimana(giorni)
        self.assertEqual((settimana[0]['nome'], settimana[0]['lavaggi'], settimana[0]['lavaggi_persona']),
                         ('Martedì', Decimal('1'), None))         # nessuna presenza quel giorno
        with mock.patch('apps.personale.previsione._meteo', return_value={date(2026, 10, 6): {'pioggia': 3, 'ore': 4}}):
            self.assertEqual([m['nome'] for m in per_meteo(giorni, OTTOBRE, fine)], ['Pioggia'])
        with mock.patch('apps.personale.previsione._meteo', return_value=None):
            self.assertIsNone(per_meteo(giorni, OTTOBRE, fine))
        cera = ServizioProdotto.objects.create(titolo='Cera', categoria=self.categoria, prezzo=Decimal('12.20'), descrizione='')
        ordine = Ordine.objects.get()
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=cera, quantita=1, prezzo_unitario=cera.prezzo)
        m = mix_ed_extra(OTTOBRE, fine)
        self.assertEqual((m['lavaggi'], m['con_extra'], m['pct_extra'], m['ricavo_extra']),
                         (1, 1, Decimal('100'), Decimal('10')))
        self.assertEqual((m['mix'][0]['nome'], m['mix'][0]['ricavo']), ('Lavaggio a mano', Decimal('100')))

    def test_pareggio_e_persone_consigliate(self):
        from .produttivita import kpi_mese, pareggio, persone_consigliate
        q = pareggio(kpi_mese(OTTOBRE, 'scontrini'), Decimal('30'))
        self.assertEqual((q['ricavo'], q['consumi'], q['mdc']), (Decimal('100'), Decimal('30'), Decimal('70')))
        self.assertEqual(round(q['costo_gp'], 2), Decimal('213.33'))     # (190 - 30) / 0,75 giorni-persona
        self.assertEqual(round(q['pareggio'], 2), Decimal('3.05'))       # 213,33 / 70
        self.assertEqual(round(q['obiettivo'], 2), Decimal('5.33'))      # 213,33 / (70 - 0,3 x 100)
        self.assertEqual(round(q['effettivi'], 2), Decimal('1.33'))
        self.assertIsNone(pareggio(kpi_mese(OTTOBRE, 'scontrini'), Decimal('80'))['obiettivo'])   # 70 - 80 < 0
        righe = persone_consigliate([{'lavaggi': Decimal('6')}], q['obiettivo'])
        self.assertEqual(righe[0]['consigliate'], 2)                     # 6 / 5,33 = 1,1 -> 2

    def test_pagina(self):
        from unittest import mock
        VisteAnaliticaTest.setUp(self)
        with mock.patch('apps.personale.previsione._meteo', return_value={}):
            for url in ('/contabilita/analitica/produttivita/?anno=2026', '/contabilita/analitica/produttivita/?anno=2026&mese=10'):
                r = VisteAnaliticaTest.apri(self, url)
                self.assertEqual(r.status_code, 200, url)
                self.assertContains(r, 'Auto per ora-persona')
                self.assertContains(r, 'Pareggio del servito')


class CapacitaTest(TestCase):
    """Servito di prova: costo pieno 190, consumi 30, 6 ore pagate, un
    lavaggio a mano da 100 netti."""
    def setUp(self):
        from apps.core.models import ServizioProdotto
        AnaliticaTest.setUp(self)
        self.a_mano = ServizioProdotto.objects.get(titolo='Lavaggio a mano')

    def test_capacita_e_costo_del_servizio(self):
        self.a_mano.minuti_persona_standard = 120
        self.a_mano.save()
        c = analitica([OTTOBRE], 'scontrini')['servito']['capacita']
        self.assertEqual((c['ore_pagate'], c['ore_utili'], c['copertura']), (Decimal('6'), Decimal('2'), Decimal('100')))
        self.assertEqual(round(c['saturazione'], 2), Decimal('33.33'))
        self.assertEqual(round(c['costo_ora'], 4), Decimal('26.6667'))         # (190 - 30) / 6
        s = c['servizi'][0]
        self.assertEqual((s['ricavo'], s['consumi']), (Decimal('100'), Decimal('30')))
        self.assertEqual(round(s['costo'], 2), Decimal('83.33'))               # 30 + 2 ore x 26,67
        # costo dei servizi venduti + capacita' non usata = costo pieno
        self.assertEqual(round(s['costo'] * s['pezzi'] + c['non_usata'], 2), Decimal('190.00'))
        self.assertEqual(round(s['prezzo_obiettivo'], 2), Decimal('145.24'))   # 83,33 / 0,7 x 1,22

    def test_copertura_parziale(self):
        from apps.core.models import ServizioProdotto
        from apps.ordini.models import ItemOrdine, Ordine
        self.a_mano.minuti_persona_standard = 60
        self.a_mano.save()
        cera = ServizioProdotto.objects.create(titolo='Cera', categoria=self.categoria, prezzo=Decimal('12.20'), descrizione='')
        ItemOrdine.objects.create(ordine=Ordine.objects.get(), servizio_prodotto=cera, quantita=1, prezzo_unitario=cera.prezzo)
        c = analitica([OTTOBRE], 'scontrini')['servito']['capacita']
        self.assertEqual((c['pezzi'], c['pezzi_std'], c['copertura'], c['consumi']), (2, 1, Decimal('50'), Decimal('15')))
        senza = next(s for s in c['servizi'] if s['nome'] == 'Cera')
        self.assertEqual((senza['lavoro'], senza['costo'], senza['prezzo_obiettivo']), (None, None, None))

    def test_unione_degli_intervalli(self):
        from .capacita import _unisci
        ora = timezone.make_aware(datetime(2026, 10, 6, 10, 0))
        minuti = lambda m: ora + timedelta(minutes=m)
        # due righe uguali (due postazioni scelte) e una che si sovrappone: 0-20 e 15-30 -> 30 minuti
        self.assertEqual(_unisci([(minuti(0), minuti(20)), (minuti(0), minuti(20)), (minuti(15), minuti(30))]), 1800)
        self.assertEqual(_unisci([(minuti(0), minuti(10)), (minuti(20), minuti(25))]), 900)

    def test_minuti_rilevati_solo_ordini_con_un_servizio(self):
        from unittest import mock

        from apps.ordini.models import Ordine

        from .capacita import minuti_rilevati
        ordine = Ordine.objects.get()
        with mock.patch('apps.contabilita.capacita.minuti_per_ordine', return_value={ordine.pk: 42.0}):
            self.assertEqual(minuti_rilevati(), {self.a_mano.pk: (42, 1)})

    def test_impostazioni_minuti_e_obiettivo(self):
        from .models import ParametriAnalitica
        VisteAnaliticaTest.setUp(self)
        self.assertContains(VisteAnaliticaTest.apri(self, '/contabilita/analitica/impostazioni/'), f'min_{self.a_mano.pk}')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'minuti', f'min_{self.a_mano.pk}': '35'})
        self.a_mano.refresh_from_db()
        self.assertEqual(self.a_mano.minuti_persona_standard, 35)
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'minuti', f'min_{self.a_mano.pk}': ''})
        self.a_mano.refresh_from_db()
        self.assertIsNone(self.a_mano.minuti_persona_standard)
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'obiettivo', 'margine_obiettivo': '25'})
        self.assertEqual(ParametriAnalitica.attuali().margine_obiettivo, Decimal('25'))
        self.assertEqual(VisteAnaliticaTest.apri(self, '/contabilita/analitica/?anno=2026&mese=10').status_code, 200)


class ClientiAbbonamentiTest(TestCase):
    """Mese di prova di AnaliticaTest (costo pieno del servito 190 su un
    lavaggio) con un cliente e un abbonamento mensile."""
    def setUp(self):
        from apps.clienti.models import Cliente
        from apps.core.models import ServizioProdotto
        from apps.ordini.models import ItemOrdine, Ordine
        AnaliticaTest.setUp(self)
        self.a_mano = ServizioProdotto.objects.get(titolo='Lavaggio a mano')
        self.cliente = Cliente.objects.create(tipo='privato', nome='Cliente', cognome='Inventato', telefono='3330000000')
        Ordine.objects.update(cliente=self.cliente)
        # un lavaggio precedente dello stesso cliente, ad agosto
        agosto = Ordine.objects.create(cliente=self.cliente, totale=Decimal('61'), totale_finale=Decimal('61'))
        ItemOrdine.objects.create(ordine=agosto, servizio_prodotto=self.a_mano, quantita=1, prezzo_unitario=Decimal('61'))
        Ordine.objects.filter(pk=agosto.pk).update(data_ora=timezone.make_aware(datetime(2026, 8, 6, 10, 0)),
                                                   totale=Decimal('61'), totale_finale=Decimal('61'))

    def abbonamento(self):
        from apps.abbonamenti.models import AccessoAbbonamento, Abbonamento, ConfigurazioneAbbonamento
        conf = ConfigurazioneAbbonamento.objects.create(titolo='Mensile prova', descrizione='', prezzo=Decimal('30.50'),
                                                        modalita_targa='libera', periodicita_reset='mensile',
                                                        durata='1_mese', giorni_durata=30)
        abb = Abbonamento.objects.create(cliente=self.cliente, configurazione=conf, data_scadenza=date(2026, 10, 15))
        Abbonamento.objects.filter(pk=abb.pk).update(data_attivazione=date(2026, 9, 16))
        for giorno in (3, 10):
            a = AccessoAbbonamento.objects.create(abbonamento=abb, servizio=self.a_mano, metodo_verifica='qr', autorizzato=True)
            AccessoAbbonamento.objects.filter(pk=a.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, giorno, 9, 0)))
        rifiutato = AccessoAbbonamento.objects.create(abbonamento=abb, servizio=self.a_mano, metodo_verifica='qr')
        AccessoAbbonamento.objects.filter(pk=rifiutato.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 11, 9, 0)))

    def test_abbonamenti_competenza_e_costo(self):
        from .clienti_abbonamenti import clienti_e_abbonamenti
        self.abbonamento()
        self.a_mano.minuti_persona_standard = 120
        self.a_mano.save()
        ab = clienti_e_abbonamenti([OTTOBRE], 'scontrini')['abbonamenti']
        g = ab['righe'][0]
        # 25 netti su 30 giorni, 15 giorni in ottobre
        self.assertEqual((g['abbonamenti'], g['venduti'], g['ricavo'], g['accessi']), (1, 0, Decimal('12.5'), 2))
        self.assertEqual(round(g['costo'], 2), Decimal('166.67'))          # 2 accessi x 83,33
        self.assertEqual(g['in_perdita'], 1)
        self.assertEqual(ab['totale']['ricavo_accesso'], Decimal('6.25'))
        # senza minuti standard: costo pieno medio per lavaggio (190)
        self.a_mano.minuti_persona_standard = None
        self.a_mano.save()
        g = clienti_e_abbonamenti([OTTOBRE], 'scontrini')['abbonamenti']['righe'][0]
        self.assertEqual(g['costo'], Decimal('380'))

    def test_clienti(self):
        from .clienti_abbonamenti import clienti
        k = clienti(OTTOBRE, date(2026, 10, 31))
        self.assertEqual((k['lavaggi'], k['con_cliente'], k['copertura'], k['attivi'], k['nuovi']),
                         (1, 1, Decimal('100'), 1, 0))                     # primo lavaggio ad agosto
        self.assertEqual((k['ritorno'], k['intervallo'], k['ricavo_cliente']), (Decimal('100'), 61, Decimal('100')))
        self.assertEqual(k['fasce'][0]['clienti'], 1)
        self.assertEqual(clienti(date(2026, 8, 1), date(2026, 8, 31))['nuovi'], 1)

    def test_pagina(self):
        VisteAnaliticaTest.setUp(self)
        self.abbonamento()
        for url in ('/contabilita/analitica/clienti/?anno=2026', '/contabilita/analitica/clienti/?anno=2026&mese=10',
                    '/contabilita/analitica/clienti/?anno=2099'):
            self.assertEqual(VisteAnaliticaTest.apri(self, url).status_code, 200, url)
        self.assertContains(VisteAnaliticaTest.apri(self, '/contabilita/analitica/clienti/?anno=2026&mese=10'), 'Mensile prova')


class BudgetTest(TestCase):
    """Effettivo di ottobre: 1 lavaggio a 100, consumi 30, 6 ore a 10 euro,
    altri fissi 100: risultato -90."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        AnaliticaTest.setUp(self)

    def budget(self, **campi):
        from .models import BudgetServito
        valori = dict(lavaggi=2, ricavo_lavaggio=Decimal('90'), consumi_lavaggio=Decimal('25'), ore_pagate=Decimal('5'),
                      costo_orario=Decimal('12'), altri_fissi=Decimal('80'))
        valori.update(campi)
        return BudgetServito.objects.create(mese=OTTOBRE, **valori)

    def test_grandezze_e_scostamenti(self):
        from .budget import budget_del_mese, grandezze, risultato, scostamenti
        from .produttivita import kpi_mese
        e = grandezze(kpi_mese(OTTOBRE, 'scontrini'))
        self.assertEqual((e['lavaggi'], e['ricavo_lavaggio'], e['consumi_lavaggio'], e['ore_pagate'], e['costo_orario'],
                          e['altri_fissi']), (1, Decimal('100'), Decimal('30'), Decimal('6'), Decimal('10'), Decimal('100')))
        self.assertEqual(risultato(e), Decimal('-90'))
        self.assertIsNone(budget_del_mese(OTTOBRE, 'scontrini'))          # niente budget e niente 2025
        b = budget_del_mese(OTTOBRE, 'scontrini', self.budget())
        self.assertEqual(risultato(b), Decimal('-10'))                    # 2 x 65 - 60 - 80
        s = scostamenti(e, b)
        self.assertEqual((s['volume'], s['prezzo'], s['consumi_volume'], s['consumi_spesa']),
                         (Decimal('-90'), Decimal('10'), Decimal('25'), Decimal('-5')))
        self.assertEqual((s['ore_attese'], s['ore_volume'], s['efficienza'], s['costo_orario'], s['fissi']),
                         (Decimal('2.5'), Decimal('30'), Decimal('-42'), Decimal('12'), Decimal('-20')))
        totale = sum((v for k, v in s.items() if k != 'ore_attese'), ZERO)
        self.assertEqual(totale, risultato(e) - risultato(b))             # -80

    def test_campi_vuoti_dall_anno_prima(self):
        from unittest import mock

        from .budget import budget_del_mese
        prima = {'lavaggi': 4, 'ricavi': Decimal('80'), 'variabili': Decimal('8'), 'ore': Decimal('10'),
                 'personale': Decimal('90'), 'pieno': Decimal('150'), 'abbonamento': 0, 'giorni_persona': ZERO,
                 'giorni_aperti': 1}
        with mock.patch('apps.contabilita.produttivita.kpi_mese', return_value=prima):
            b = budget_del_mese(OTTOBRE, 'scontrini', self.budget(lavaggi=None, costo_orario=None))
        self.assertEqual((b['lavaggi'], b['costo_orario'], b['ricavo_lavaggio']), (4, Decimal('9'), Decimal('90')))

    def test_periodo_e_pagina(self):
        from .budget import budget_e_scostamenti
        from .models import BudgetServito
        vuoto = budget_e_scostamenti([OTTOBRE], 2026, 'scontrini')
        self.assertIsNone(vuoto['scostamenti'])
        self.assertEqual(vuoto['saltati'], ['Ott'])
        self.budget()
        r = budget_e_scostamenti([OTTOBRE], 2026, 'scontrini')
        self.assertEqual((r['risultato_bdg'], r['risultato_eff'], r['contati']), (Decimal('-10'), Decimal('-90'), ['Ott']))
        self.assertEqual(sum((s['valore'] for s in r['scostamenti']), ZERO), Decimal('-80'))
        VisteAnaliticaTest.setUp(self)
        for url in ('/contabilita/analitica/budget/?anno=2026', '/contabilita/analitica/budget/?anno=2026&mese=10'):
            self.assertEqual(VisteAnaliticaTest.apri(self, url).status_code, 200, url)
        self.client.post('/contabilita/analitica/budget/?anno=2026', {'b_11_lavaggi': '300', 'b_11_ricavo_lavaggio': '24,50',
                                                                      'b_12_lavaggi': '1,5'})
        nov = BudgetServito.objects.get(mese=date(2026, 11, 1))
        self.assertEqual((nov.lavaggi, nov.ricavo_lavaggio, nov.ore_pagate), (300, Decimal('24.50'), None))
        self.assertFalse(BudgetServito.objects.filter(mese=date(2026, 12, 1)).exists())   # 1,5 lavaggi: errore
        self.assertFalse(BudgetServito.objects.filter(mese=OTTOBRE).exists())             # riga vuota: tolto


class AnnoPrimaIncompletoTest(TestCase):
    """L'anno prima con lavaggi ma senza presenze (come il 2025): non fa da
    budget e non si confronta sulle righe di costi e ore."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        AnaliticaTest.setUp(self)

    def kpi(self, mese, base):
        from .produttivita import kpi_mese as originale
        if mese.year == 2025:
            return {'lavaggi': 2, 'abbonamento': 0, 'ore': ZERO, 'giorni_persona': ZERO, 'giorni_aperti': 1,
                    'ricavi': Decimal('50'), 'variabili': ZERO, 'personale': ZERO, 'pieno': Decimal('5')}
        return originale(mese, base)

    def test_niente_budget_e_niente_confronto_sui_costi(self):
        from unittest import mock

        from . import produttivita
        from .budget import budget_del_mese
        originale = produttivita.kpi_mese
        with mock.patch('apps.contabilita.produttivita.kpi_mese',
                        side_effect=lambda m, b: self.kpi(m, b) if m.year == 2025 else originale(m, b)):
            self.assertIsNone(budget_del_mese(OTTOBRE, 'scontrini'))
            t = produttivita.andamento(2026, 'scontrini')
        righe = {r['chiave']: r['celle'][9] for r in t['righe']}
        self.assertEqual(righe['lavaggi']['var'], Decimal('-50'))          # 1 contro 2: si confronta
        self.assertIsNone(righe['costo_lavaggio']['var'])                  # 2025 senza presenze
        self.assertIsNone(righe['costo_lavaggio']['prima'])
