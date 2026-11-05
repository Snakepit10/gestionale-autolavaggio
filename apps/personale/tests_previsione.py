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
POLVERE_VERA = prev._polvere
CICLI_VERI = prev._cicli_self_per_giorno


def dati_sintetici():
    """Storico da agosto 2025: 20 lavaggi i feriali, 32 il sabato, chiuso
    la domenica. I clienti guardano la previsione: se e' prevista pioggia
    il giorno stesso i lavaggi si dimezzano, se e' prevista il giorno dopo
    calano di un quarto, anche quando poi non piove (1 previsione su 3 e'
    un falso allarme). Squadra sempre piena: 6 persone da 8 ore il sabato,
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
            fattore = (0.5 if previsto[g]['piove'] else 1) * (0.75 if previsto[g + timedelta(days=1)]['piove'] else 1)
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
        self.meteo, self.polvere = meteo, polvere
        patch = [
            mock.patch.object(prev, '_meteo', return_value=meteo),
            mock.patch.object(prev, '_meteo_previsto_storico',
                              side_effect=lambda dal, al: {g: v for g, v in previsto.items() if dal <= g <= al}),
            mock.patch.object(prev, '_lavaggi_per_giorno', side_effect=lambda dal, al: {g: v for g, v in lavaggi.items() if dal <= g <= al}),
            mock.patch.object(prev, '_presenze_per_giorno', side_effect=lambda dal, al: {g: v for g, v in presenze.items() if dal <= g <= al}),
            mock.patch.object(prev, '_costo_orario_per_mese', return_value={date(2026, 10, 1): 15.0}),
            mock.patch.object(prev, '_polvere', side_effect=lambda dal, al: {g: v for g, v in polvere.items() if dal <= g <= al}),
            mock.patch.object(prev, '_piste_per_giorno', return_value={}),
            mock.patch.object(prev, '_cicli_self_per_giorno',
                              side_effect=lambda dal, al: {g: v for g, v in cicli.items() if dal <= g <= al}),
        ]
        for p in patch:
            p.start()
            self.addCleanup(p.stop)

    def test_impara_pioggia_e_giorni(self):
        r = prev.calcola(OGGI)
        v = r['verifica']
        self.assertLess(v['errore_pct'], 15)
        self.assertLess(v['errore_pct'], v['errore_base_pct'])        # meglio della sola media per giorno
        self.assertLess(v['errore_pct'], v['errore_prima_pct'] * 0.7)  # la pioggia prevista conta
        self.assertTrue(r['previsioni_storiche'])
        self.assertLessEqual(v['squadra_scarsa'], 15)
        effetti = {e['nome']: e['effetto'] for e in r['effetti']}
        self.assertNotIn('Domenica, rispetto a un feriale', effetti)            # chiuso la domenica
        self.assertLess(abs(effetti['Pioggia prevista ma non caduta'] + 50), 10)  # i clienti guardano la previsione
        self.assertLess(abs(effetti['Pioggia prevista domani'] + 25), 10)
        self.assertLess(abs(effetti['Sabato, rispetto a un feriale'] - 60), 10)
        # cicli self previsti: ~30 asciutto, ~8 con pioggia
        for p in [p for p in r['previsioni'] if not p['chiuso']]:
            self.assertLess(abs(p['cicli'] - (8 if p['piove'] else 30)), 6, p)
        self.assertLess(v['errore_cicli_pct'], 15)
        # la sabbia di ieri fa lavare: il modello la usa e ne vede l'effetto
        self.assertTrue(r['usa_segnali'])
        self.assertLess(v['errore_pct'], v['errore_senza_segnali_pct'])
        self.assertLess(abs(effetti['Sabbia del Sahara ieri'] - 40), 12)
        self.assertAlmostEqual(r['ore_persona'], 8.0)
        # 7 lavaggi per persona: 20 lavaggi = 3 al servito; senza soglia sempre 1 al self
        par = ParametriPrevisione.attuali()
        self.assertEqual(r['esempio_20'], 3)
        self.assertEqual(prev.squadra_per(20, 30, par), 4)
        self.assertEqual(prev.squadra_per(21, None, par), 4)
        self.assertEqual(prev.squadra_per(1, 0, par), 2)                   # minimo 1 al servito + self
        par.soglia_cicli_self = 20
        self.assertEqual(prev.squadra_per(20, 30, par), 4)
        self.assertEqual(prev.squadra_per(20, 10, par), 3)                 # pochi cicli: nessuno solo al self
        self.assertEqual(r['chiuso'], ['Dom'])
        per_giorno = {p['data']: p for p in r['previsioni']}
        domenica = next(p for p in r['previsioni'] if p['data'].weekday() == 6)
        self.assertTrue(domenica['chiuso'])
        self.assertIsNone(domenica['squadra'])
        oggi = r['previsioni'][0]
        self.assertTrue(oggi['oggi'])
        self.assertEqual(oggi['data'], OGGI)
        self.assertTrue(r['previsioni'][1]['domani'])
        aperti = [p for p in per_giorno.values() if not p['chiuso']]
        for p in aperti:
            dopo = self.meteo[p['data'] + timedelta(days=1)]['piove']
            atteso = (32 if p['data'].weekday() == 5 else 20) * (0.5 if p['piove'] else 1) * (0.75 if dopo else 1)
            atteso *= 1.4 if self.polvere[p['data'] - timedelta(days=1)] > 100 else 1
            self.assertLess(abs(p['previsti'] - atteso) / atteso, 0.2, p)
            self.assertEqual(p['in_arrivo'], sum(1 for k in (1, 2, 3) if self.meteo[p['data'] + timedelta(days=k)]['piove']))
        # squadra sempre piena: nei giorni di pioggia si risparmia
        self.assertGreater(v['ore_risparmiate'], 0)
        self.assertGreater(v['risparmio'], 0)

    def test_pioggia_debole_conta(self):
        # pomeriggio di pioviggine: pochi millimetri ma e' una giornata di pioggia
        self.assertTrue(prev.piove({'pioggia': 0.4, 'ore': 5, 'codice': 51}))
        self.assertTrue(prev.piove({'pioggia': 0.3, 'ore': 1, 'codice': 3, 'probabilita': 75}))
        self.assertTrue(prev.piove({'pioggia': 0.2, 'ore': 0, 'codice': 61}))
        self.assertFalse(prev.piove({'pioggia': 0.1, 'ore': 1, 'codice': 3, 'probabilita': 20}))
        self.assertFalse(prev.piove({}))

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
        self.assertEqual((dopodomani['fonte'], dopodomani['piove']), ('Open-Meteo', False))

    def test_registra_solo_dopo_le_18(self):
        r = prev.calcola(OGGI)
        sera = timezone.make_aware(datetime(2026, 11, 5, 18, 30))
        mattina = timezone.make_aware(datetime(2026, 11, 5, 10, 0))
        with mock.patch('django.utils.timezone.localtime', return_value=mattina):
            self.assertIsNone(prev.registra(r))
        with mock.patch('django.utils.timezone.localtime', return_value=sera):
            salvata = prev.registra(r)
        self.assertEqual(salvata.data, date(2026, 11, 6))
        self.assertEqual(PrevisioneSquadra.objects.count(), 1)

    def test_soglia_cicli_self(self):
        par = ParametriPrevisione.attuali()
        par.soglia_cicli_self = 20
        par.save()
        r = prev.calcola(OGGI, par)
        for p in [p for p in r['previsioni'] if not p['chiuso']]:
            self.assertEqual(p['self'], 0 if p['piove'] else 1, p)
            self.assertEqual(p['squadra'], p['servito'] + p['self'])
        v = r['verifica']
        self.assertLessEqual(v['self_scarso'], 12)
        self.assertGreaterEqual(v['self_giusto'], 85)
        self.assertIsNotNone(v['servito_giusto'])

    def test_salva_parametri(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare2', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        self.client.force_login(utente)
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '6,5', 'soglia_cicli_self': '25'})
        par = ParametriPrevisione.attuali()
        self.assertEqual((float(par.lavaggi_per_persona), par.soglia_cicli_self), (6.5, 25))
        self.client.post('/personale/previsione/', {'lavaggi_per_persona': '7', 'soglia_cicli_self': ''})
        self.assertIsNone(ParametriPrevisione.attuali().soglia_cicli_self)

    def test_segnali_inutili_esclusi(self):
        # sabbia a caso, senza effetto sui lavaggi: non deve peggiorare il modello
        rnd = random.Random(3)
        with mock.patch.object(prev, '_polvere', side_effect=lambda dal, al: {
                dal + timedelta(days=k): rnd.uniform(0, 200) for k in range((al - dal).days + 1)}):
            r = prev.calcola(OGGI)
        self.assertLess(r['verifica']['errore_pct'], 15)

    def test_andamento(self):
        serie = {OGGI - timedelta(days=k): (20, 0) for k in range(5, 40)}
        self.assertEqual(prev._andamento(serie, OGGI), 0.0)                       # mancano i giorni recenti
        serie.update({OGGI - timedelta(days=k): (26, 0) for k in (2, 3, 4)})
        self.assertGreater(prev._andamento(serie, OGGI), 0.15)                     # ~ +20% sul solito
        self.assertEqual(prev._andamento({}, OGGI), 0.0)

    def test_profilo_self(self):
        from apps.finanze.models import TransazionePortale
        sabato = date(2026, 10, 31)
        for n, (ora, origine) in enumerate([(9, 'contanti'), (9, 'contanti'), (10, 'contanti'), (15, 'contanti'),
                                            (9, 'unita')]):                    # l'ultima e' del servito
            TransazionePortale.objects.create(portale='A', numero=n + 1, programma=4, origine=origine,
                                              orario=timezone.make_aware(datetime.combine(sabato, time(ora, 5))))
        pr = prev.profilo_self(OGGI)
        riga = next(x for x in pr['righe'] if x['gruppo'] == 'Sabato')
        self.assertEqual(riga['giorni'], 1)
        self.assertEqual(riga['medie'][pr['ore'].index(9)], 2)
        mattina, pomeriggio, sera = riga['fasce']
        self.assertEqual((mattina['cicli'], pomeriggio['cicli'], sera['cicli']), (3, 1, 0))
        self.assertEqual(prev._fasce_previste(sabato + timedelta(days=7), 40, pr)[0]['cicli'], 30)   # 3/4 la mattina

    def test_cicli_self_una_sola_fonte(self):
        from apps.core.models import Categoria, ServizioProdotto
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica, TransazionePortale
        from apps.ordini.models import ItemOrdine, Ordine
        portale = Cassa.objects.create(nome='Portale', tipo='automatica', tracking_washcycles=True)
        completo = ServizioProdotto.objects.create(titolo='Lavaggio completo', categoria=Categoria.objects.create(nome='S'),
                                                   prezzo=22, descrizione='')
        prima, dopo = date(2026, 10, 1), date(2026, 10, 2)
        ChiusuraCassaAutomatica.objects.create(cassa=portale, data=prima, wash_cycles=30)
        ordine = Ordine.objects.create(totale=0, totale_finale=0)
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 1, 10, 0)))
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=completo, prezzo_unitario=22, quantita=4)
        ChiusuraCassaAutomatica.objects.create(cassa=portale, data=dopo, wash_cycles=4)
        for n, origine in enumerate(['contanti', 'contanti', 'contanti', 'unita']):
            TransazionePortale.objects.create(portale='B', numero=n + 1, programma=4, origine=origine,
                                              orario=timezone.make_aware(datetime(2026, 10, 2, 9 + n, 0)))
        # pochi giorni WashTec: tutto dalle chiusure (meno i completi), niente miscugli
        self.assertEqual(CICLI_VERI(prima, dopo), {prima: (26, 0.0), dopo: (4, 0.0)})
        with mock.patch.object(prev, 'MIN_GIORNI_WASHTEC', 1):
            self.assertEqual(CICLI_VERI(prima, dopo), {dopo: (3, 0.0)})          # solo WashTec
        # giorno importato a meta' (2 transazioni su 30 cicli della chiusura): scartato
        self.assertFalse(prev._washtec_completo({'self_washtec': 2, 'servito_washtec': 0, 'chiusure': 30, 'completi': 0}))
        self.assertTrue(prev._washtec_completo({'self_washtec': 20, 'servito_washtec': 6, 'chiusure': 30, 'completi': 0}))

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

    def test_pochi_dati(self):
        with mock.patch.object(prev, '_lavaggi_per_giorno', return_value={}):
            self.assertIn('90 giorni', prev.calcola(OGGI)['errore'])

    def test_pagina(self):
        from apps.auth_system.models import PermessoGruppo
        utente = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        utente.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='personale', gruppo=gruppo)
        self.client.force_login(utente)
        with mock.patch('django.utils.timezone.localdate', return_value=OGGI):
            risposta = self.client.get('/personale/previsione/')
        self.assertEqual(risposta.status_code, 200)
        self.assertContains(risposta, 'Squadra consigliata domani')
        self.assertContains(risposta, 'Oggi Gio 05/11')
        self.assertContains(risposta, 'Quanto e')
