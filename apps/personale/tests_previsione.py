import random
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from . import previsione as prev
from .models import ParametriPrevisione, PrevisioneSquadra

OGGI = date(2026, 11, 5)
METEO_VERO = prev._meteo          # prima dei mock del setUp
GIORNO_PRIMA_VERO = prev._meteo_giorno_prima
CICLI_SELF_VERO = prev._cicli_self
OPERATORI_VERI = prev._cicli_operatori
NUVOLE_VERE = prev._nuvole
ALTRI_MODELLI_VERO = prev._pioggia_altri_modelli
POLVERE_VERA = prev._polvere


def dati_sintetici():
    """Storico da agosto 2025: 20 lavaggi i feriali, 32 il sabato, chiuso
    la domenica. I clienti guardano la previsione: se e' prevista pioggia
    il giorno stesso i lavaggi si dimezzano, anche quando poi non piove
    (1 previsione su 3 e' un falso allarme). Squadra sempre piena: 6 persone da 8 ore il sabato,
    5 gli altri giorni. Cicli self: 30, 8 con pioggia prevista. Dopo una
    giornata di sabbia del Sahara i lavaggi salgono del 40%."""
    rnd = random.Random(1)
    meteo, previsto, lavaggi, presenze, cicli, polvere = {}, {}, {}, {}, {}, {}
    g = prev.INIZIO_ORDINI - timedelta(days=3)
    while g <= OGGI + timedelta(days=12):
        pioggia = rnd.choice([0, 0, 0, 0, 0, 0, 5, 15])
        meteo[g] = {'pioggia': float(pioggia), 'ore': 6.0 if pioggia else 0.0, 'tmax': 25.0}
        falso_allarme = not pioggia and rnd.random() < 0.12
        previsto[g] = {'pioggia': 8.0 if pioggia or falso_allarme else 0.0, 'ore': 5.0 if pioggia or falso_allarme else 0.0,
                       'tmax': 25.0}
        for m in (meteo[g], previsto[g]):
            m['piove'] = prev.piove(m)
        polvere[g] = 150.0 if rnd.random() < 0.06 else rnd.uniform(0, 15)
        g += timedelta(days=1)
    g = prev.INIZIO_ORDINI
    while g < OGGI:
        if g.weekday() != 6:
            base = 32 if g.weekday() == 5 else 20
            fattore = 0.5 if previsto[g]['piove'] else 1
            fattore *= 1.4 if polvere[g - timedelta(days=1)] > 100 else 1
            lavaggi[g] = (max(1, round(base * fattore * rnd.uniform(0.95, 1.05))), 0.0)
            cicli[g] = (max(0, round((8 if previsto[g]['piove'] else 30) * rnd.uniform(0.9, 1.1))), 0.0)
            if g >= date(2026, 1, 1):
                presenze[g] = (48.0, 6) if g.weekday() == 5 else (40.0, 5)
        g += timedelta(days=1)
    # da oggi in poi la previsione attuale e' quella "vera"
    for g in list(meteo):
        if g >= OGGI:
            meteo[g] = previsto[g]
    return meteo, previsto, lavaggi, presenze, cicli, polvere


class PrevisioneTest(TestCase):
    def setUp(self):
        meteo, previsto, lavaggi, presenze, cicli, polvere = dati_sintetici()
        self.meteo, self.polvere, self.previsto = meteo, polvere, previsto
        patch = [
            mock.patch.object(prev, '_meteo', return_value=meteo),
            mock.patch.object(prev, '_meteo_giorno_prima',
                              side_effect=lambda dal, al, *modelli: {g: v for g, v in previsto.items() if dal <= g <= al}),
            mock.patch.object(prev, '_meteo_previsto_storico',
                              side_effect=lambda dal, al, *modelli: {g: v for g, v in previsto.items() if dal <= g <= al}),
            mock.patch.object(prev, '_lavaggi_per_giorno', side_effect=lambda dal, al: {g: v for g, v in lavaggi.items() if dal <= g <= al}),
            mock.patch.object(prev, '_presenze_per_giorno', side_effect=lambda dal, al: {g: v for g, v in presenze.items() if dal <= g <= al}),
            mock.patch.object(prev, '_costo_orario_per_mese', return_value={date(2026, 10, 1): 15.0}),
            mock.patch.object(prev, '_polvere', side_effect=lambda dal, al: {g: v for g, v in polvere.items() if dal <= g <= al}),
            mock.patch.object(prev, '_piste_per_giorno', return_value={}),
            mock.patch.object(prev, '_pioggia_altri_modelli', return_value=None),
            mock.patch.object(prev, '_nuvole', return_value={}),
            mock.patch.object(prev, '_cicli_self', side_effect=lambda dal, al: {
                g: (v[0], 0) for g, v in cicli.items() if dal <= g <= al and v[0] >= prev.MIN_CICLI_SELF}),
            mock.patch.object(prev, '_cicli_operatori', side_effect=lambda dal, al: {
                g: (v[0] // 2, 0) for g, v in cicli.items() if dal <= g <= al and v[0] >= prev.MIN_CICLI_SELF}),
        ]
        for p in patch:
            p.start()
            self.addCleanup(p.stop)

    def test_impara_pioggia_e_giorni(self):
        r = prev.calcola(OGGI)
        v = r['verifica']
        self.assertLess(v['errore_pct'], 15)
        self.assertLess(v['errore_pct'], v['errore_base_pct'])        # meglio della sola media per giorno
        self.assertLess(v['errore_pct'], v['errore_senza_pioggia_pct'] * 0.7)  # la pioggia prevista conta
        self.assertLess(v['limite_pct'], 20)
        self.assertTrue(r['previsioni_giorno_prima'])
        self.assertEqual(r['margine_pct'], 10)
        effetti = {e['nome']: e['effetto'] for e in r['effetti']}
        self.assertNotIn('Domenica, rispetto a un feriale', effetti)            # chiuso la domenica
        self.assertLess(abs(effetti['Giornata di pioggia prevista'] + 50), 12)  # i clienti guardano la previsione
        self.assertLess(abs(effetti['Sabato, rispetto a un feriale'] - 60), 20)  # la regolarizzazione lo smorza
        # senza soglia al self sempre una persona; i cicli previsti seguono la pioggia
        self.assertTrue(all(p['self'] == 1 and p['cicli_self'] is not None and p['cicli_operatori'] is not None
                            for p in r['previsioni']))
        self.assertLess(abs(r['verifica_operatori']['totale_pct'] - 100), 8)
        s = r['verifica_self']
        self.assertLess(abs(s['totale_pct'] - 100), 8)                     # totali in pari
        self.assertLess(s['errore_settimana_pct'], s['errore_pct'] + 1)
        self.assertLess(s['errore_pct'], 25)
        # la sabbia di ieri fa lavare: il modello ne vede l'effetto
        self.assertLess(abs(effetti['Sabbia del Sahara ieri'] - 40), 12)
        self.assertAlmostEqual(r['ore_persona'], 8.0)
        # 7 lavaggi per persona: 20 lavaggi = 3 al servito, piu' sempre 1 al self
        par = ParametriPrevisione.attuali()
        self.assertEqual(r['esempio_20'], 3)
        self.assertEqual(prev.squadra_per(20, par), 4)
        self.assertEqual(prev.squadra_per(21, par), 4)
        self.assertEqual(prev.squadra_per(22, par), 5)
        self.assertEqual(prev.squadra_per(1, par), 2)                      # minimo 1 al servito + self
        self.assertEqual(r['chiuso'], ['Dom'])
        per_giorno = {p['data']: p for p in r['previsioni']}
        domenica = next(p for p in r['previsioni'] if p['data'].weekday() == 6)
        self.assertTrue(domenica['chiuso'])                                # servito chiuso...
        self.assertEqual((domenica['servito'], domenica['squadra']), (0, 1))  # ...al self resta una persona
        oggi = r['previsioni'][0]
        self.assertTrue(oggi['oggi'])
        self.assertEqual(oggi['data'], OGGI)
        self.assertTrue(r['previsioni'][1]['domani'])
        aperti = [p for p in per_giorno.values() if not p['chiuso']]
        for p in aperti:
            atteso = (32 if p['data'].weekday() == 5 else 20) * (0.5 if p['piove'] else 1)
            atteso *= 1.4 if self.polvere[p['data'] - timedelta(days=1)] > 100 else 1
            self.assertLess(abs(p['previsti'] - atteso) / atteso, 0.2, p)
            self.assertAlmostEqual(p['alti'], p['previsti'] * 1.1)
        # squadra sempre piena: seguendo il modello si risparmia
        c = v['confronto']
        self.assertGreater(c['risparmiate'], 0)
        self.assertEqual(c['voi']['persone'] - c['modello']['persone'], c['risparmiate'])
        self.assertAlmostEqual(c['euro'], c['risparmiate'] * r['ore_persona'] * 15.0)

    def test_pioggia_debole_conta(self):
        # pomeriggio di pioviggine: pochi millimetri ma e' una giornata di pioggia, debole
        self.assertTrue(prev.piove({'pioggia': 0.4, 'ore': 5}))
        self.assertTrue(prev.debole({'pioggia': 0.4, 'ore': 5}))
        self.assertTrue(prev.piove({'pioggia': 3.0, 'ore': 1}))
        self.assertFalse(prev.debole({'pioggia': 3.0, 'ore': 1}))
        self.assertFalse(prev.piove({'pioggia': 0.2, 'ore': 0, 'codice': 61, 'probabilita': 75}))
        self.assertFalse(prev.piove({}))

    def test_previsione_del_giorno_prima(self):
        risposta = {'hourly': {'time': ['2026-11-03T09:00', '2026-11-03T10:00', '2026-11-03T11:00', '2026-11-04T09:00'],
                               'precipitation_previous_day1': [0.5, 0.05, 1.0, None]}}
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta):
            meteo = GIORNO_PRIMA_VERO(date(2026, 11, 3), date(2026, 11, 4))
        self.assertEqual(list(meteo), [date(2026, 11, 3)])
        self.assertEqual((round(meteo[date(2026, 11, 3)]['pioggia'], 2), meteo[date(2026, 11, 3)]['ore']), (1.55, 2))
        self.assertTrue(meteo[date(2026, 11, 3)]['piove'])

    def test_meteo_da_open_meteo(self):
        # due modelli: ItaliaMeteo per oggi, il modello automatico per dopodomani (fuori dalla sua portata)
        risposta = {'daily': {'time': ['2026-11-05', '2026-11-07'],
                              'precipitation_sum_italia_meteo_arpae_icon_2i': [3.2, None],
                              'precipitation_sum_best_match': [0.1, 0.0],
                              'precipitation_hours_italia_meteo_arpae_icon_2i': [5.0, None],
                              'precipitation_hours_best_match': [1.0, 0.0],
                              'weather_code_best_match': [61, 1], 'temperature_2m_max_best_match': [19.0, 21.0],
                              'precipitation_probability_max_best_match': [80, 5]}}
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta), \
             mock.patch('django.utils.timezone.localdate', return_value=OGGI):
            meteo = METEO_VERO(OGGI, OGGI)
        self.assertEqual(meteo[OGGI]['probabilita'], 80)
        self.assertEqual((meteo[OGGI]['pioggia'], meteo[OGGI]['ore']), (3.2, 5.0))   # dal modello italiano
        self.assertEqual(meteo[OGGI]['fonte'], 'ItaliaMeteo ICON-2I')
        self.assertTrue(meteo[OGGI]['piove'])
        dopodomani = meteo[OGGI + timedelta(days=2)]
        self.assertEqual((dopodomani['fonte'], dopodomani['piove']), ('Open-Meteo automatico', False))

    def test_registra_a_turno_finito(self):
        r = prev.calcola(OGGI)
        sera = timezone.make_aware(datetime(2026, 11, 5, 19, 10))
        in_turno = timezone.make_aware(datetime(2026, 11, 5, 18, 30))
        with mock.patch('django.utils.timezone.localtime', return_value=in_turno):
            self.assertIsNone(prev.registra(r))
        with mock.patch('django.utils.timezone.localtime', return_value=sera):
            salvata = prev.registra(r)
            self.assertEqual(salvata.cicli_self, r['previsioni'][1]['cicli_self'])
            self.assertEqual(salvata.cicli_operatori, r['previsioni'][1]['cicli_operatori'])
            previsti = salvata.lavaggi_previsti
            r['previsioni'][1]['previsti'] += 5                   # riaperta piu' tardi, meteo piu' fresco
            self.assertEqual(prev.registra(r).lavaggi_previsti, previsti)     # la prima resta
        self.assertEqual(salvata.data, date(2026, 11, 6))
        self.assertEqual(PrevisioneSquadra.objects.count(), 1)

    def test_a_turno_finito_domani_conta_oggi(self):
        reale = prev._lavaggi_per_giorno

        def con_oggi(dal, al):
            return {**reale(dal, al), **({OGGI: (200, 0.0)} if dal <= OGGI <= al else {})}
        with mock.patch.object(prev, '_lavaggi_per_giorno', side_effect=con_oggi):
            with mock.patch.object(prev, 'turno_finito', return_value=False):
                in_turno = prev.calcola(OGGI)['previsioni']
            with mock.patch.object(prev, 'turno_finito', return_value=True):
                finito = prev.calcola(OGGI)['previsioni']
        self.assertEqual(finito[0]['previsti'], in_turno[0]['previsti'])      # oggi non cambia
        self.assertGreater(finito[1]['previsti'], in_turno[1]['previsti'])    # domani si'

    def test_soglia_persona_al_self(self):
        par = ParametriPrevisione.attuali()
        par.cicli_persona_self = 10
        for g, pioggia in ((OGGI + timedelta(days=1), True), (OGGI + timedelta(days=2), False)):
            self.meteo[g] = {'pioggia': 8.0 if pioggia else 0.0, 'ore': 5.0 if pioggia else 0.0, 'tmax': 25.0, 'piove': pioggia}
        previsioni = {p['data']: p for p in prev.calcola(OGGI, par)['previsioni']}
        bagnato, asciutto = previsioni[OGGI + timedelta(days=1)], previsioni[OGGI + timedelta(days=2)]
        self.assertLess(bagnato['cicli_operatori'], 10)
        self.assertGreater(bagnato['cicli_self'], bagnato['cicli_operatori'])              # la soglia guarda gli operatori
        self.assertEqual((bagnato['self'], bagnato['squadra']), (0, bagnato['servito']))     # ci pensa il servito
        self.assertGreaterEqual(asciutto['cicli_operatori'], 10)
        self.assertEqual(asciutto['self'], 1)
        domenica = next(p for p in previsioni.values() if p['chiuso'])                     # servito chiuso: resta coperto
        self.assertEqual(domenica['self'], 1)
        self.assertEqual(prev.persone_self(None, 20), 1)                                   # senza stima resta la persona

    def test_cicli_self_dai_dati(self):
        giorni = {date(2026, 3, 1): {'self_washtec': 20, 'servito_washtec': 30, 'chiusure': 55, 'completi': 10, 'servito_portale': 12},  # WashTec
                  date(2026, 3, 2): {'self_washtec': 5, 'servito_washtec': 5, 'chiusure': 40, 'completi': 10, 'servito_portale': 12},    # incompleto
                  date(2026, 3, 3): {'self_washtec': 0, 'servito_washtec': 12, 'chiusure': None, 'completi': 10, 'servito_portale': 12},  # fermo
                  date(2026, 3, 4): {'self_washtec': 6, 'servito_washtec': 10, 'chiusure': None, 'completi': 10, 'servito_portale': 12}}  # solo contanti
        with mock.patch.object(prev, '_cicli_dettaglio', return_value=giorni):
            self.assertEqual(CICLI_SELF_VERO(date(2026, 3, 1), date(2026, 3, 4)),
                             {date(2026, 3, 1): (38, 0), date(2026, 3, 2): (30, 0), date(2026, 3, 4): (6, 0)})
            self.assertEqual(OPERATORI_VERI(date(2026, 3, 1), date(2026, 3, 4)),
                             {date(2026, 3, 1): (18, 0), date(2026, 3, 4): (0, 0)})

    def test_nuvole_previste(self):
        giorni = ['2026-11-03T%02d:00' % h for h in range(24)]
        def scarica(url, params, durata, modelli=None, chiave_dati='daily'):
            if 'previous-runs' in url:
                self.assertEqual(params['end_date'], '2026-11-04')                     # fino a ieri
                return {'hourly': {'time': giorni, 'cloud_cover_previous_day1': [100 if 8 <= h <= 18 else 0 for h in range(24)]}}
            return {'hourly': {'time': ['2026-11-05T%02d:00' % h for h in range(24)], 'cloud_cover': [50] * 24}}
        with (mock.patch('django.utils.timezone.localdate', return_value=OGGI),
              mock.patch.object(prev, '_scarica', side_effect=scarica)):
            self.assertEqual(NUVOLE_VERE(date(2026, 11, 1), OGGI + timedelta(days=1)),
                             {date(2026, 11, 3): 1.0, OGGI: 0.5})
        with (mock.patch('django.utils.timezone.localdate', return_value=OGGI),
              mock.patch.object(prev, '_scarica', return_value=None)):                 # servizio giu': si va avanti
            self.assertEqual(NUVOLE_VERE(date(2026, 11, 1), OGGI), {})

    def test_il_cielo_coperto_abbassa_i_cicli(self):
        coperto, sereno = OGGI + timedelta(days=4), OGGI + timedelta(days=5)           # martedi' e mercoledi'
        for g in (coperto, sereno):
            self.meteo[g] = {'pioggia': 0.0, 'ore': 0.0, 'tmax': 25.0, 'piove': False}
        cicli = {g: 30 if not self.previsto[g]['piove'] else 8 for g in self.previsto}
        nuvole = {g: (1.0 if v == 8 else 0.1) for g, v in cicli.items() if g < OGGI}
        nuvole.update({coperto: 1.0, sereno: 0.1})
        with mock.patch.object(prev, '_nuvole', return_value=nuvole):
            previsioni = {p['data']: p for p in prev.calcola(OGGI)['previsioni']}
        self.assertLess(previsioni[coperto]['cicli_self'], previsioni[sereno]['cicli_self'])
        self.assertLess(previsioni[coperto]['cicli_operatori'], previsioni[sereno]['cicli_operatori'])

    def test_servito_al_portale(self):
        from apps.core.models import Categoria, ServizioProdotto
        from apps.finanze.models import AbbinamentoPortale, LavaggioSenzaPortale, TransazionePortale
        from apps.ordini.models import ItemOrdine, Ordine
        giorno = timezone.make_aware(datetime(2026, 3, 1, 10, 0))
        categoria = Categoria.objects.create(nome='Lavaggi di prova')
        portale = ServizioProdotto.objects.create(titolo='Esterno prova', categoria=categoria, prezzo=10, programmi_portale='8,9,7,4')
        interno = ServizioProdotto.objects.create(titolo='Interno prova', categoria=categoria, prezzo=10)

        def item(servizio, quantita=1, stato='completato'):
            ordine = Ordine.objects.create(totale=10, totale_finale=10, stato=stato)
            Ordine.objects.filter(pk=ordine.pk).update(data_ora=giorno)
            return ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=servizio, quantita=quantita, prezzo_unitario=10)

        item(portale, quantita=2)                                                      # due cicli
        item(interno)                                                                  # non passa dal portale
        item(portale, stato='annullato')
        LavaggioSenzaPortale.objects.create(item=item(portale))                        # chiuso senza portale
        manuale = item(interno)                                                        # abbinato a mano
        for numero, origine in enumerate(['unita'] * 5 + ['contanti'], start=1):
            TransazionePortale.objects.create(portale='A', numero=numero, orario=giorno, programma=4, origine=origine)
        AbbinamentoPortale.objects.create(transazione=TransazionePortale.objects.get(numero=1), item=manuale)
        c = prev._cicli_dettaglio(giorno.date(), giorno.date())[giorno.date()]
        self.assertEqual(c['servito_portale'], 3)
        self.assertEqual((prev._cicli_operatori_giorno(c), prev._cicli_self_giorno(c)), (2, 3))

    def test_salva_parametri(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare2', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        self.client.force_login(utente)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '6,5'})
        self.assertEqual(float(ParametriPrevisione.attuali().lavaggi_per_persona), 6.5)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': 'sette'})          # non valido: resta
        self.assertEqual(float(ParametriPrevisione.attuali().lavaggi_per_persona), 6.5)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'margine_pct': '15'})
        self.assertEqual(float(ParametriPrevisione.attuali().margine_pct), 15)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'margine_pct': '90'})   # troppo: resta
        self.assertEqual(float(ParametriPrevisione.attuali().margine_pct), 15)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'cicli_persona_self': '30'})
        self.assertEqual(ParametriPrevisione.attuali().cicli_persona_self, 30)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'cicli_persona_self': 'tanti'})   # resta
        self.assertEqual(ParametriPrevisione.attuali().cicli_persona_self, 30)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'cicli_persona_self': ''})        # vuoto: sempre
        self.assertIsNone(ParametriPrevisione.attuali().cicli_persona_self)

    def test_errore_vero(self):
        previsioni = [PrevisioneSquadra(data=OGGI, lavaggi_previsti=20, lavaggi_alti=22, squadra=4),
                      PrevisioneSquadra(data=OGGI, lavaggi_previsti=10, lavaggi_alti=11, squadra=3),
                      PrevisioneSquadra(data=OGGI, lavaggi_previsti=9, lavaggi_alti=10, squadra=1, chiuso=True)]
        storico = [{'p': previsioni[0], 'reali': 25}, {'p': previsioni[1], 'reali': 15}, {'p': previsioni[2], 'reali': 3}]
        self.assertEqual(prev.errore_vero(storico), {'giorni': 2, 'errore_pct': 25.0})    # 10 su 40, senza il chiuso
        self.assertIsNone(prev.errore_vero([]))

    def test_profilo_self(self):
        from apps.finanze.models import TransazionePortale
        sabato = date(2026, 10, 31)
        from apps.core.models import Categoria, ServizioProdotto
        from apps.finanze.models import AbbinamentoPortale
        from apps.ordini.models import ItemOrdine, Ordine
        for n, (ora, origine) in enumerate([(9, 'contanti'), (9, 'unita'), (10, 'contanti'), (15, 'contanti'),
                                            (9, 'unita')]):
            t = TransazionePortale.objects.create(portale='A', numero=n + 1, programma=4, origine=origine,
                                                  orario=timezone.make_aware(datetime.combine(sabato, time(ora, 5))))
        # l'ultima e' abbinata a un lavaggio del servito: non e' self
        servizio = ServizioProdotto.objects.create(titolo='Lavaggio completo', categoria=Categoria.objects.create(nome='S'),
                                                   prezzo=22, descrizione='')
        item = ItemOrdine.objects.create(ordine=Ordine.objects.create(totale=0, totale_finale=0),
                                         servizio_prodotto=servizio, prezzo_unitario=22)
        AbbinamentoPortale.objects.create(transazione=t, item=item)
        pr = prev.profilo_self(OGGI)
        riga = next(x for x in pr['righe'] if x['gruppo'] == 'Sabato')
        self.assertEqual(riga['giorni'], 1)
        self.assertEqual(riga['medie'][pr['ore'].index(9)], 2)
        mattina, pomeriggio, sera = riga['fasce']
        self.assertEqual((mattina['cicli'], pomeriggio['cicli'], sera['cicli']), (3, 1, 0))

    def test_esporta_csv(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare3', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        self.client.force_login(utente)
        with mock.patch('django.utils.timezone.localdate', return_value=OGGI):
            risposta = self.client.get('/personale/previsione/dati.csv')
        testo = risposta.content.decode('utf-8-sig')
        righe = testo.strip().splitlines()
        self.assertTrue(righe[0].startswith('data;giorno;festivo;lavaggi_servito'))
        self.assertEqual(len(righe) - 1, (OGGI - prev.INIZIO_ORDINI).days)
        self.assertIn('2026-11-04;Mer', testo)

    def test_polvere_da_open_meteo(self):
        risposta = {'hourly': {'time': ['2026-11-05T10:00', '2026-11-05T14:00', '2026-11-06T10:00'],
                               'dust': [40.0, 180.0, None]}}
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta):
            polvere = POLVERE_VERA(OGGI, OGGI + timedelta(days=1))
        self.assertEqual(polvere, {OGGI: 180.0})

    def test_confronto_meteo(self):
        # i clienti guardano le previsioni di ECMWF: gli altri servizi sbagliano un giorno su tre
        rnd = random.Random(5)
        sbagliate = {g: dict(v, piove=(not v['piove']) if rnd.random() < 0.33 else v['piove'])
                     for g, v in self.previsto.items()}
        for v in sbagliate.values():
            v['pioggia'], v['ore'] = (8.0, 5.0) if v['piove'] else (0.0, 0.0)

        def giorno_prima(dal, al, modelli=('best_match',)):
            fonte = self.previsto if modelli[0] == 'ecmwf_ifs025' else sbagliate
            nome = prev.NOMI_MODELLI[modelli[0]]
            return {g: dict(v, fonte=nome) for g, v in fonte.items() if dal <= g <= al}
        with mock.patch.object(prev, '_meteo_giorno_prima', side_effect=giorno_prima):
            righe = prev.confronto_meteo(OGGI)
        self.assertEqual(len(righe), len(prev.MODELLI_DISPONIBILI))
        self.assertEqual(righe[0]['modello'], 'ecmwf_ifs025')                   # il piu' preciso in cima
        self.assertLess(righe[0]['errore_pct'], righe[1]['errore_pct'])
        self.assertEqual(righe[0]['copertura'], 100)
        self.assertTrue(next(r for r in righe if r['modello'] == 'italia_meteo_arpae_icon_2i')['scelto'])

    def test_giorno_prima_piu_modelli(self):
        # ICON-2I per le prime ore, poi solo il modello automatico
        risposta = {'hourly': {'time': ['2026-11-05T10:00', '2026-11-05T11:00', '2026-11-05T12:00'],
                               'precipitation_previous_day1_italia_meteo_arpae_icon_2i': [1.0, 0.5, None],
                               'precipitation_previous_day1_best_match': [0.0, 0.0, 0.3]}}
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta):
            meteo = GIORNO_PRIMA_VERO(OGGI, OGGI, prev.modelli_per('italia_meteo_arpae_icon_2i'))
        self.assertAlmostEqual(meteo[OGGI]['pioggia'], 1.8)
        self.assertEqual((meteo[OGGI]['ore'], meteo[OGGI]['fonte']), (3, 'ItaliaMeteo ICON-2I'))

    def test_scegli_modello_meteo(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare4', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        self.client.force_login(utente)
        self.client.post('/personale/previsione/', {'modello_meteo': 'ecmwf_ifs025'})
        self.assertEqual(ParametriPrevisione.attuali().modello_meteo, 'ecmwf_ifs025')
        self.client.post('/personale/previsione/', {'modello_meteo': 'inventato'})            # ignorato
        self.assertEqual(ParametriPrevisione.attuali().modello_meteo, 'ecmwf_ifs025')
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from .views import previsione
        richiesta = RequestFactory().get('/personale/previsione/?confronta=1')   # il client non rende i template su Python 3.14
        richiesta.user, richiesta.session = utente, {}
        richiesta._messages = FallbackStorage(richiesta)
        with mock.patch('django.utils.timezone.localdate', return_value=OGGI):
            risposta = previsione(richiesta)
        self.assertContains(risposta, 'Confronto dei servizi meteo')
        self.assertContains(risposta, 'NOAA GFS')
        self.assertContains(risposta, 'Turni risparmiati')
        self.assertEqual(prev.modelli_per('ecmwf_ifs025'), ('ecmwf_ifs025', 'best_match'))
        self.assertEqual(prev.modelli_per('best_match'), ('best_match',))

    def test_avviso_se_gli_altri_modelli_danno_pioggia(self):
        domani = OGGI + timedelta(days=1)
        self.meteo[domani] = {'pioggia': 0.0, 'ore': 0.0, 'tmax': 20.0, 'piove': False}

        def avviso():
            return next(p for p in prev.calcola(OGGI)['previsioni'] if p['domani']).get('avviso_pioggia')
        with mock.patch.object(prev, '_pioggia_altri_modelli', return_value={'pioggia': 5, 'modelli': 9}) as altri:
            self.assertEqual(avviso(), {'pioggia': 5, 'modelli': 9})
        altri.assert_called_once_with(domani, 'italia_meteo_arpae_icon_2i')
        with mock.patch.object(prev, '_pioggia_altri_modelli', return_value={'pioggia': 4, 'modelli': 9}):
            self.assertIsNone(avviso())
        self.meteo[domani] = {'pioggia': 5.0, 'ore': 4.0, 'tmax': 20.0, 'piove': True}      # gia' prevista: niente avviso
        with mock.patch.object(prev, '_pioggia_altri_modelli', return_value={'pioggia': 9, 'modelli': 9}) as altri:
            self.assertIsNone(avviso())
        altri.assert_not_called()

    def test_pioggia_altri_modelli(self):
        risposta = {'hourly': {'time': ['2026-12-01T10:00', '2026-12-01T11:00'],
                               'precipitation_ecmwf_ifs025': [0.5, 0.6],             # 1,1 mm: pioggia
                               'precipitation_gfs_seamless': [0.2, 0.1],             # 2 ore: pioggia
                               'precipitation_gem_seamless': [0.05, 0.0],            # asciutto
                               'precipitation_knmi_seamless': [None, None]}}         # non risponde: non conta
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta) as http:
            altri = ALTRI_MODELLI_VERO(date(2026, 12, 1), 'italia_meteo_arpae_icon_2i')
        self.assertEqual(altri, {'pioggia': 2, 'modelli': 3})
        self.assertNotIn('italia_meteo_arpae_icon_2i', http.call_args[0][0])         # il servizio in uso e' escluso
        with mock.patch('apps.finanze.weather._http_get_json', return_value=None):
            self.assertIsNone(ALTRI_MODELLI_VERO(date(2026, 12, 2), 'best_match'))

    def test_pochi_dati(self):
        with mock.patch.object(prev, '_lavaggi_per_giorno', return_value={}):
            self.assertIn('90 giorni', prev.calcola(OGGI)['errore'])

    def test_pagina(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from .views import previsione
        richiesta = RequestFactory().get('/personale/previsione/')        # il client non rende i template su Python 3.14
        richiesta.user, richiesta.session = utente, {}
        richiesta._messages = FallbackStorage(richiesta)
        with (mock.patch('django.utils.timezone.localdate', return_value=OGGI),
              mock.patch.object(prev, '_pioggia_altri_modelli', return_value={'pioggia': 6, 'modelli': 9}),
              mock.patch.object(prev, '_cicli_dettaglio', return_value={OGGI: {
                  'self_washtec': 4, 'servito_washtec': 5, 'chiusure': None, 'completi': 2, 'servito_portale': 2}})):
            self.meteo[OGGI + timedelta(days=1)] = {'pioggia': 0.0, 'ore': 0.0, 'tmax': 20.0, 'piove': False}
            risposta = previsione(richiesta)
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, '6 modelli meteo su 9 danno pioggia')
        self.assertContains(risposta, 'Risparmio seguendo il modello')
        self.assertNotContains(risposta, 'econdo parere')
        self.assertContains(risposta, 'Squadra consigliata domani')
        self.assertContains(risposta, 'di margine sui lavaggi previsti')
        self.assertContains(risposta, 'il caso da solo vale circa')
        self.assertContains(risposta, 'Oggi Gio 05/11')
        self.assertContains(risposta, 'Quanto e')
        self.assertContains(risposta, 'cicli operatori previsti da cui serve la persona al self')
        self.assertNotContains(risposta, 'cicli self previsti')               # le card servono solo per il personale
        self.assertContains(risposta, 'id="cicli-self"')
        self.assertContains(risposta, 'finora 7</span>')
        self.assertContains(risposta, 'Totale dei prossimi 7 giorni')
        self.assertContains(risposta, 'di pioggia')
        self.assertContains(risposta, 'cicli operatori previsti &middot; finora 3 cicli')
        self.assertContains(risposta, 'Errore sulla settimana')
        self.assertNotContains(risposta, 'soglia_cicli_self')
