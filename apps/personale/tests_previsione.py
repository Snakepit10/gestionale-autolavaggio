import random
from datetime import date, datetime, timedelta
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from . import previsione as prev
from .models import PrevisioneSquadra

OGGI = date(2026, 11, 5)


def dati_sintetici():
    """Storico da agosto 2025: 20 lavaggi i feriali, 32 il sabato, chiuso
    la domenica; la pioggia dimezza i lavaggi. Squadra sempre piena:
    6 persone da 8 ore il sabato, 4 gli altri giorni, anche con la pioggia."""
    rnd = random.Random(1)
    meteo, lavaggi, presenze = {}, {}, {}
    g = prev.INIZIO_ORDINI - timedelta(days=3)
    while g <= OGGI + timedelta(days=8):
        pioggia = rnd.choice([0, 0, 0, 0, 0, 0, 5, 15])
        meteo[g] = {'pioggia': float(pioggia), 'tmax': 25.0}
        if prev.INIZIO_ORDINI <= g < OGGI and g.weekday() != 6:
            base = 32 if g.weekday() == 5 else 20
            lavaggi[g] = (max(1, round(base * (0.5 if pioggia else 1) * rnd.uniform(0.9, 1.1))), 0.0)
            if g >= date(2026, 1, 1):
                presenze[g] = (48.0, 6) if g.weekday() == 5 else (32.0, 4)
        g += timedelta(days=1)
    return meteo, lavaggi, presenze


class PrevisioneTest(TestCase):
    def setUp(self):
        meteo, lavaggi, presenze = dati_sintetici()
        self.meteo = meteo
        patch = [
            mock.patch.object(prev, '_meteo', return_value=meteo),
            mock.patch.object(prev, '_lavaggi_per_giorno', side_effect=lambda dal, al: {g: v for g, v in lavaggi.items() if dal <= g <= al}),
            mock.patch.object(prev, '_presenze_per_giorno', side_effect=lambda dal, al: {g: v for g, v in presenze.items() if dal <= g <= al}),
            mock.patch.object(prev, '_costo_orario_per_mese', return_value={date(2026, 10, 1): 15.0}),
        ]
        for p in patch:
            p.start()
            self.addCleanup(p.stop)

    def test_impara_pioggia_e_giorni(self):
        r = prev.calcola(OGGI)
        v = r['verifica']
        self.assertLess(v['errore_pct'], 15)
        self.assertLess(v['errore_pct'], v['errore_base_pct'])        # meglio della sola media per giorno
        self.assertAlmostEqual(r['ore_persona'], 8.0)
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
            atteso = (32 if p['data'].weekday() == 5 else 20) * (0.5 if p['pioggia'] else 1)
            self.assertLess(abs(p['previsti'] - atteso) / atteso, 0.2, p)
        # la pioggia riduce la squadra
        asciutto = [p['squadra'] for p in aperti if not p['pioggia'] and p['data'].weekday() < 5]
        bagnato = [p['squadra'] for p in aperti if p['pioggia'] and p['data'].weekday() < 5]
        if asciutto and bagnato:
            self.assertLess(max(bagnato), min(asciutto))
        # squadra sempre piena: nei giorni di pioggia si risparmia
        self.assertGreater(v['ore_risparmiate'], 0)
        self.assertGreater(v['risparmio'], 0)

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
