"""Test abbinamento lavaggi servito <-> transazioni portale unita'."""
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Categoria, ServizioProdotto
from apps.finanze.models import (AbbinamentoPortale, ChiusuraPortali,
                                 TransazionePortale)
from apps.finanze.services import abbinamento_portali as ap
from apps.ordini.models import ItemOrdine, Ordine


def ora(s):
    return timezone.make_aware(datetime.strptime(f'2026-10-06 {s}', '%Y-%m-%d %H:%M'))


class AbbinamentoPortaliTest(TestCase):
    def setUp(self):
        cat = Categoria.objects.create(nome='Servito')
        self.completo = ServizioProdotto.objects.create(
            titolo='Lavaggio Completo - Media', categoria=cat,
            prezzo=Decimal('22'), descrizione='', programmi_portale='4,7,8,9')
        self.a_mano = ServizioProdotto.objects.create(
            titolo='Lavaggio Completo a mano', categoria=cat,
            prezzo=Decimal('25'), descrizione='', programmi_portale='5')
        self.chiusura = ChiusuraPortali.objects.create(
            data=datetime(2026, 10, 6).date(),
            periodo_da=timezone.make_aware(datetime(2026, 10, 5, 19, 0)),
            periodo_a=ora('18:00'))
        self.n_tx = 0

    def _item(self, servizio, alle, quantita=1):
        ordine = Ordine.objects.create(totale=Decimal('22'),
                                       totale_finale=Decimal('22'))
        return ItemOrdine.objects.create(
            ordine=ordine, servizio_prodotto=servizio, quantita=quantita,
            prezzo_unitario=servizio.prezzo, inizio_lavorazione=ora(alle))

    def _tx(self, alle, programma, origine='unita'):
        self.n_tx += 1
        return TransazionePortale.objects.create(
            portale='A', numero=13000 + self.n_tx, orario=ora(alle),
            programma=programma, origine=origine)

    def test_orario_piu_vicino(self):
        item = self._item(self.completo, '10:00')
        self._tx('09:00', 4)
        vicina = self._tx('10:10', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(len(proposte), 1)
        self.assertEqual(proposte[0]['item'].pk, item.pk)
        self.assertEqual(proposte[0]['transazione'].pk, vicina.pk)
        self.assertEqual(proposte[0]['delta_min'], 10)

    def test_programma_incompatibile_e_contanti_esclusi(self):
        self._item(self.a_mano, '10:00')       # solo P5
        self._tx('10:05', 4)                    # P4: incompatibile
        self._tx('10:05', 5, origine='contanti')  # contanti: mai usate
        self.assertEqual(ap.proponi(self.chiusura), [])
        giusta = self._tx('10:20', 5)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual([p['transazione'].pk for p in proposte], [giusta.pk])

    def test_tolleranza_e_finestra(self):
        self._item(self.completo, '10:00')
        self._tx('13:00', 4)                    # oltre 2h
        TransazionePortale.objects.create(      # fuori finestra
            portale='B', numero=13999, orario=ora('19:00'),
            programma=4, origine='unita')
        self.assertEqual(ap.proponi(self.chiusura), [])

    def test_quantita_due_prende_due_transazioni(self):
        self._item(self.completo, '10:00', quantita=2)
        self._tx('10:05', 4)
        self._tx('10:20', 7)
        self._tx('10:40', 8)
        self.assertEqual(len(ap.proponi(self.chiusura)), 2)

    def test_greedy_non_riusa_la_stessa_transazione(self):
        self._item(self.completo, '10:00')
        self._item(self.completo, '10:10')
        self._tx('10:05', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(len(proposte), 1)

    def test_riepilogo_residuo(self):
        self._item(self.completo, '10:00')
        self._tx('10:05', 4)                    # abbinata al servito
        self._tx('15:00', 1)                    # residuo 15 euro
        self._tx('16:00', 7)                    # residuo senza prezzo
        r = ap.riepilogo(self.chiusura)
        self.assertEqual(r['n_proposte'], 1)
        self.assertEqual(len(r['residuo']), 2)
        self.assertEqual(r['valore_residuo'], Decimal('15.00'))
        self.assertEqual(r['residuo_senza_prezzo'], 1)

    def test_conferma_idempotente(self):
        self._item(self.completo, '10:00')
        self._tx('10:05', 4)
        self.assertEqual(ap.conferma_proposte(self.chiusura), 1)
        self.assertEqual(ap.conferma_proposte(self.chiusura), 0)
        self.assertEqual(AbbinamentoPortale.objects.count(), 1)
        r = ap.riepilogo(self.chiusura)
        self.assertEqual((r['n_confermati'], r['n_proposte']), (1, 0))

    def test_abbina_manuale_validazioni(self):
        item = self._item(self.a_mano, '10:00')
        p4 = self._tx('10:05', 4)
        p5 = self._tx('11:30', 5)
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, p4.pk)
        self.assertFalse(ok)                    # programma incompatibile
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, p5.pk)
        self.assertTrue(ok)
        item2 = self._item(self.a_mano, '11:40')
        ok, _ = ap.abbina_manuale(self.chiusura, item2.pk, p5.pk)
        self.assertFalse(ok)                    # transazione gia' usata

    def test_endpoint_conferma_e_rimuovi(self):
        user = User.objects.create_user('op', 'op@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        self._item(self.completo, '10:00')
        self._tx('10:05', 4)
        url = reverse('finanze:azione_abbinamento_portali')
        r = self.client.post(url, {'data': '2026-10-06', 'azione': 'conferma'})
        self.assertEqual(r.status_code, 302)
        abb = AbbinamentoPortale.objects.get()
        r = self.client.post(url, {'data': '2026-10-06', 'azione': 'rimuovi',
                                   'abbinamento_id': abb.pk})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(AbbinamentoPortale.objects.exists())

    def test_lista_programmi_portale(self):
        s = ServizioProdotto(programmi_portale=' 4, 7,x,12,9 ')
        self.assertEqual(s.lista_programmi_portale, {4, 7, 9})
