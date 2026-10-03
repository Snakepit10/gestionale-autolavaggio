"""Test del report conversioni web (marcatori [PROMO ...])."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clienti.models import Cliente
from apps.ordini.models import Ordine
from apps.prenotazioni.models import Prenotazione, SlotPrenotazione

from .services.conversioni_web import estrai_marker, report_conversioni_web


class EstraiMarkerTest(TestCase):
    def test_marker_completo(self):
        self.assertEqual(
            estrai_marker('nota libera\n[PROMO garanzia_pioggia | facebook/cpc/lancio]'),
            ('garanzia_pioggia', 'facebook/cpc/lancio'))

    def test_marker_senza_utm(self):
        self.assertEqual(estrai_marker('[PROMO garanzia_pioggia]'),
                         ('garanzia_pioggia', 'diretto'))

    def test_senza_marker(self):
        self.assertIsNone(estrai_marker('solo una nota'))
        self.assertIsNone(estrai_marker(''))


class ReportConversioniWebTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo='privato', nome='Conv', cognome='Webtest',
            telefono='3390000001')
        oggi = timezone.localdate()
        self.slot = SlotPrenotazione.objects.create(
            data=oggi + timedelta(days=1),
            ora_inizio='09:00', ora_fine='09:30',
            max_prenotazioni=5)

    def _pren(self, stato='in_attesa', nota='[PROMO garanzia_pioggia | fb/cpc/lancio]',
              ordine=None):
        return Prenotazione.objects.create(
            cliente=self.cliente, slot=self.slot, stato=stato,
            durata_stimata_minuti=30, nota_cliente=nota, ordine=ordine)

    def test_funnel_e_fatturato(self):
        ordine = Ordine.objects.create(
            cliente=self.cliente, totale=Decimal('25.00'),
            totale_finale=Decimal('25.00'),
            importo_pagato=Decimal('25.00'), stato_pagamento='pagato')
        self._pren(stato='completata', ordine=ordine)
        self._pren(stato='confermata')
        self._pren(stato='annullata')
        self._pren(nota='senza marker')  # fuori dal report
        self._pren(nota='[PROMO altra_promo]')  # gruppo separato

        oggi = timezone.localdate()
        gruppi, totale = report_conversioni_web(
            oggi - timedelta(days=1), oggi)

        self.assertEqual(len(gruppi), 2)
        g = gruppi[0]  # il piu' numeroso
        self.assertEqual((g['promo'], g['campagna']),
                         ('garanzia_pioggia', 'fb/cpc/lancio'))
        self.assertEqual(g['n_richieste'], 3)
        self.assertEqual(g['n_confermate'], 2)
        self.assertEqual(g['stati']['annullata'], 1)
        self.assertEqual(g['n_ordini'], 1)
        self.assertEqual(g['fatturato'], Decimal('25.00'))
        self.assertEqual(g['incassato'], Decimal('25.00'))

        self.assertEqual(gruppi[1]['campagna'], 'diretto')
        self.assertEqual(totale['n_richieste'], 4)
        self.assertEqual(totale['incassato'], Decimal('25.00'))

    def test_fuori_periodo_escluse(self):
        self._pren()
        oggi = timezone.localdate()
        gruppi, totale = report_conversioni_web(
            oggi - timedelta(days=30), oggi - timedelta(days=10))
        self.assertEqual(gruppi, [])
        self.assertEqual(totale['n_richieste'], 0)

    def test_accesso_staff(self):
        # Non staff: rediretto; endpoint pagina non renderizzato qui
        # (test client + template rotti su Py3.14), basta il redirect.
        user = User.objects.create_user('cw_user', 'c@w.it', 'pw')
        self.client.force_login(user)
        r = self.client.get(reverse('marketing:conversioni-web'))
        self.assertEqual(r.status_code, 302)
