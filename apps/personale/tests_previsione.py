import random
from datetime import date, datetime, timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from . import previsione as prev
from .models import PrevisioneSquadra

OGGI = date(2026, 11, 5)
METEO_VERO = prev._meteo          # prima dei mock del setUp


def dati_sintetici():
    """Storico da agosto 2025: 20 lavaggi i feriali, 32 il sabato, chiuso
    la domenica. I clienti guardano la previsione: se e' prevista pioggia
    il giorno stesso i lavaggi si dimezzano, se e' prevista il giorno dopo
    calano di un quarto, anche quando poi non piove (1 previsione su 3 e'
    un falso allarme). Squadra sempre piena: 6 persone da 8 ore il sabato,
    5 gli altri giorni."""
    rnd = random.Random(1)
    meteo, previsto, lavaggi, presenze = {}, {}, {}, {}
    g = prev.INIZIO_ORDINI - timedelta(days=3)
    while g <= OGGI + timedelta(days=12):
        pioggia = rnd.choice([0, 0, 0, 0, 0, 0, 5, 15])
        meteo[g] = {'pioggia': float(pioggia), 'ore': 6.0 if pioggia else 0.0, 'tmax': 25.0}
        falso_allarme = not pioggia and rnd.random() < 0.12
        previsto[g] = {'pioggia': 8.0 if pioggia or falso_allarme else 0.0, 'ore': 5.0 if pioggia or falso_allarme else 0.0,
                       'tmax': 25.0}
        for m in (meteo[g], previsto[g]):
            m['piove'] = prev.piove(m)
        g += timedelta(days=1)
    g = prev.INIZIO_ORDINI
    while g < OGGI:
        if g.weekday() != 6:
            base = 32 if g.weekday() == 5 else 20
            fattore = (0.5 if previsto[g]['piove'] else 1) * (0.75 if previsto[g + timedelta(days=1)]['piove'] else 1)
            lavaggi[g] = (max(1, round(base * fattore * rnd.uniform(0.95, 1.05))), 0.0)
            if g >= date(2026, 1, 1):
                presenze[g] = (48.0, 6) if g.weekday() == 5 else (40.0, 5)
        g += timedelta(days=1)
    # da oggi in poi la previsione attuale e' quella "vera"
    for g in list(meteo):
        if g >= OGGI:
            meteo[g] = previsto[g]
    return meteo, previsto, lavaggi, presenze


class PrevisioneTest(TestCase):
    def setUp(self):
        meteo, previsto, lavaggi, presenze = dati_sintetici()
        self.meteo = meteo
        patch = [
            mock.patch.object(prev, '_meteo', return_value=meteo),
            mock.patch.object(prev, '_meteo_previsto_storico',
                              side_effect=lambda dal, al: {g: v for g, v in previsto.items() if dal <= g <= al}),
            mock.patch.object(prev, '_lavaggi_per_giorno', side_effect=lambda dal, al: {g: v for g, v in lavaggi.items() if dal <= g <= al}),
            mock.patch.object(prev, '_presenze_per_giorno', side_effect=lambda dal, al: {g: v for g, v in presenze.items() if dal <= g <= al}),
            mock.patch.object(prev, '_costo_orario_per_mese', return_value={date(2026, 10, 1): 15.0}),
            mock.patch.object(prev, '_minuti_lavaggio', return_value=60.0),
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
        self.assertAlmostEqual(r['ore_persona'], 8.0)
        # 20 lavaggi da 60 minuti = 20 ore / 6,8 ore utili a persona -> 3 al servito, + 1 al self
        self.assertEqual(r['esempio_20'], 3)
        self.assertEqual(prev.squadra_per(20, 1.0, 8.0), 4)
        self.assertEqual(prev.squadra_per(1, 1.0, 8.0), 2)                 # minimo 1 al servito + self
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
        risposta = {'daily': {'time': ['2026-11-05'], 'precipitation_sum': [0.6], 'precipitation_hours': [4.0],
                              'weather_code': [61], 'temperature_2m_max': [19.0], 'precipitation_probability_max': [80]}}
        with mock.patch('apps.finanze.weather._http_get_json', return_value=risposta), \
             mock.patch('django.utils.timezone.localdate', return_value=OGGI):
            meteo = METEO_VERO(OGGI, OGGI)
        self.assertEqual(meteo[OGGI]['probabilita'], 80)
        self.assertTrue(meteo[OGGI]['piove'])

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
