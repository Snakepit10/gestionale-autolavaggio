"""Test abbinamento lavaggi servito <-> transazioni portale unita'."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from apps.auth_system.testutils import utente_titolare
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
            prezzo=Decimal('22'), descrizione='', programmi_portale='8,9,7,4')
        self.a_mano = ServizioProdotto.objects.create(
            titolo='Lavaggio Completo a mano', categoria=cat,
            prezzo=Decimal('25'), descrizione='', programmi_portale='5')
        self.chiusura = ChiusuraPortali.objects.create(
            data=datetime(2026, 10, 6).date(),
            periodo_da=timezone.make_aware(datetime(2026, 10, 5, 19, 0)),
            periodo_a=ora('18:00'))
        self.n_tx = 0

    def _item(self, servizio, completato=None, creato=None, quantita=1):
        """Item servito completato alle `completato` (ordine creato 1h
        prima) oppure, senza completamento, creato alle `creato`."""
        ordine = Ordine.objects.create(totale=Decimal('22'),
                                       totale_finale=Decimal('22'))
        creazione = ora(creato) if creato else ora(completato) - timedelta(hours=1)
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=creazione)
        return ItemOrdine.objects.create(
            ordine=ordine, servizio_prodotto=servizio, quantita=quantita,
            prezzo_unitario=servizio.prezzo,
            fine_lavorazione=ora(completato) if completato else None)

    def _tx(self, alle, programma, origine='unita'):
        self.n_tx += 1
        return TransazionePortale.objects.create(
            portale='A', numero=13000 + self.n_tx, orario=ora(alle),
            programma=programma, origine=origine)

    def test_indietro_dal_completamento(self):
        # completato 10:30 -> obiettivo 10:00: vince la piu' vicina a 10:00
        item = self._item(self.completo, completato='10:30')
        self._tx('09:00', 4)
        vicina = self._tx('10:10', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(len(proposte), 1)
        self.assertEqual(proposte[0]['item'].pk, item.pk)
        self.assertEqual(proposte[0]['transazione'].pk, vicina.pk)
        self.assertEqual(proposte[0]['delta_min'], 20)   # 20 min prima del completamento

    def test_mai_dopo_il_completamento(self):
        self._item(self.completo, completato='10:30')
        self._tx('10:40', 8)                    # successiva al completamento
        self.assertEqual(ap.proponi(self.chiusura), [])

    def test_priorita_programma_prima_di_vicinanza(self):
        # P8 lontano batte P4 esatto: prima i P8, poi P9, P7, infine P4
        self._item(self.completo, completato='10:30')
        p8 = self._tx('09:15', 8)
        self._tx('10:00', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual([p['transazione'].pk for p in proposte], [p8.pk])
        self.assertEqual(proposte[0]['giro'], 1)

    def test_giri_a_esaurimento(self):
        # Due item, un P7 e un P4: il primo giro (P8) e il secondo (P9)
        # non trovano nulla, poi ognuno prende il suo
        self._item(self.completo, completato='10:30')
        self._item(self.completo, completato='11:30')
        self._tx('10:00', 7)
        self._tx('11:00', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(sorted(p['transazione'].programma for p in proposte), [4, 7])

    def test_ripiego_su_creazione(self):
        item = self._item(self.completo, creato='10:00')
        t = self._tx('10:20', 4)
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        self.assertEqual(r['n_senza_completamento'], 1)
        self.assertEqual(r['righe'][0]['rif_tipo'], 'creato')
        self.assertEqual(r['righe'][0]['proposte'][0]['transazione'].pk, t.pk)
        self.assertEqual(r['righe'][0]['item'].pk, item.pk)

    def test_completamento_tardivo_usa_creazione(self):
        # Creato 15:00 (stesso giorno), chiuso il giorno dopo: si usa la creazione
        ordine = Ordine.objects.create(totale=Decimal('22'),
                                       totale_finale=Decimal('22'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=ora('15:00'))
        ItemOrdine.objects.create(
            ordine=ordine, servizio_prodotto=self.a_mano, quantita=1,
            prezzo_unitario=self.a_mano.prezzo,
            fine_lavorazione=ora('15:00') + timedelta(hours=18))
        p5 = self._tx('15:25', 5)
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        self.assertEqual(r['righe'][0]['rif_tipo'], 'tardivo')
        self.assertEqual(r['righe'][0]['proposte'][0]['transazione'].pk, p5.pk)
        self.assertEqual(r['n_senza_completamento'], 1)

    def test_programma_incompatibile_e_contanti_esclusi(self):
        self._item(self.a_mano, completato='10:30')       # solo P5
        self._tx('10:00', 4)                               # incompatibile
        self._tx('10:00', 5, origine='contanti')           # contanti: mai usate
        self.assertEqual(ap.proponi(self.chiusura), [])
        giusta = self._tx('10:05', 5)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual([p['transazione'].pk for p in proposte], [giusta.pk])

    def test_tolleranza_e_giorno(self):
        self._item(self.completo, completato='17:30')
        self._tx('14:00', 7)                    # oltre 2h prima dell'obiettivo
        TransazionePortale.objects.create(      # giorno dopo: non abbinabile
            portale='B', numero=13999, orario=ora('18:30') + timedelta(days=1),
            programma=4, origine='unita')
        self.assertEqual(ap.proponi(self.chiusura), [])

    def test_servito_resta_nel_giorno_dell_ordine(self):
        # Chiusura del 06 finisce alle 18:00. Ordine delle 19:31 del 06,
        # passato al portale alle 19:40 (finestra del 07): resta nel 06.
        tardo = self._item(self.completo, creato='19:31')
        tx = self._tx('19:40', 8)
        self.assertIn(tardo.pk, [i.pk for i in ap.lavaggi_servito(self.chiusura)])
        self.assertEqual([(p['item'].pk, p['transazione'].pk) for p in ap.proponi(self.chiusura)],
                         [(tardo.pk, tx.pk)])
        # Nel report del 07 il lavaggio non c'e'; la transazione e' residuo
        # del 07 finche' non si conferma l'abbinamento col 06
        domani = ChiusuraPortali.objects.create(
            data=datetime(2026, 10, 7).date(), periodo_da=ora('18:00'),
            periodo_a=ora('18:00') + timedelta(days=1))
        r = ap.riepilogo(domani)
        self.assertEqual(r['n_servito'], 0)
        self.assertIn(tx.pk, [x['transazione'].pk for x in r['residuo']])
        ap.conferma_proposte(self.chiusura)
        self.assertNotIn(tx.pk, [x['transazione'].pk for x in ap.riepilogo(domani)['residuo']])
        # una transazione libera dopo la chiusura resta residuo del 07
        libera = self._tx('20:30', 1)
        self.assertIn(libera.pk, [x['transazione'].pk for x in ap.riepilogo(domani)['residuo']])
        self.assertNotIn(libera.pk, [x['transazione'].pk for x in ap.riepilogo(self.chiusura)['residuo']])

    def test_ripiego_p4_senza_coincidenza_orario(self):
        # Scoperto dopo i giri: prende il P4 libero piu' vicino, anche
        # dopo il completamento o a ore di distanza
        item = self._item(self.completo, completato='17:30')
        self._tx('08:00', 4)
        vicino = self._tx('12:00', 4)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(len(proposte), 1)
        self.assertEqual((proposte[0]['item'].pk, proposte[0]['transazione'].pk),
                         (item.pk, vicino.pk))
        self.assertTrue(proposte[0]['fuori_orario'])

    def test_ripiego_solo_dopo_i_giri_e_mai_a_mano(self):
        # Il P4 in orario va al primo item nel giro normale; il secondo
        # prende l'altro P4 col ripiego; il completo a mano resta scoperto
        primo = self._item(self.completo, completato='10:30')
        secondo = self._item(self.completo, completato='16:30')
        self._item(self.a_mano, completato='14:00')
        in_orario = self._tx('10:05', 4)
        lontano = self._tx('07:00', 4)
        proposte = {p['item'].pk: p for p in ap.proponi(self.chiusura)}
        self.assertEqual(proposte[primo.pk]['transazione'].pk, in_orario.pk)
        self.assertFalse(proposte[primo.pk]['fuori_orario'])
        self.assertEqual(proposte[secondo.pk]['transazione'].pk, lontano.pk)
        self.assertTrue(proposte[secondo.pk]['fuori_orario'])
        self.assertEqual(len(proposte), 2)
        self.assertEqual(ap.riepilogo(self.chiusura, con_proposte=True)['n_scoperti'], 1)

    def test_quantita_due_prende_due_transazioni(self):
        self._item(self.completo, completato='10:30', quantita=2)
        self._tx('09:50', 4)
        self._tx('10:00', 7)
        self._tx('10:05', 8)
        proposte = ap.proponi(self.chiusura)
        self.assertEqual(sorted(p['transazione'].programma for p in proposte), [7, 8])

    def test_greedy_non_riusa_la_stessa_transazione(self):
        self._item(self.completo, completato='10:30')
        self._item(self.completo, completato='10:40')
        self._tx('10:05', 4)
        self.assertEqual(len(ap.proponi(self.chiusura)), 1)

    def test_riepilogo_residuo(self):
        self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)                    # da abbinare al servito
        self._tx('15:00', 1)                    # residuo 15 euro
        self._tx('16:00', 9)                    # residuo senza prezzo
        # nessun abbinamento automatico: senza il tasto niente proposte
        r = ap.riepilogo(self.chiusura)
        self.assertEqual((r['n_proposte'], r['con_proposte'], r['n_scoperti']), (0, False, 1))
        self.assertEqual(r['righe'][0]['proposte'], [])
        self.assertEqual(len(r['residuo']), 3)
        # con il tasto: proposte visibili, il residuo non cambia finche' non si conferma
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        self.assertEqual((r['n_proposte'], len(r['residuo'])), (1, 3))
        ap.conferma_proposte(self.chiusura)
        r = ap.riepilogo(self.chiusura)
        self.assertEqual(len(r['residuo']), 2)
        self.assertEqual(r['valore_residuo'], Decimal('15.00'))
        self.assertEqual(r['residuo_senza_prezzo'], 1)

    def test_rettifica_residuo_omaggio_e_promo(self):
        from apps.finanze.models import RettificaResiduo
        omaggio = self._tx('15:00', 1)          # listino 15
        promo = self._tx('16:00', 1)            # listino 15
        self._tx('17:00', 1)                    # pagato a listino
        r = ap.riepilogo(self.chiusura)
        self.assertEqual(r['valore_residuo'], Decimal('45.00'))

        admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        self.client.force_login(admin)
        url = reverse('finanze:azione_abbinamento_portali')
        base = {'data': '2026-10-06', 'azione': 'rettifica_residuo'}
        self.client.post(url, {**base, 'transazione_id': omaggio.pk, 'motivo': 'omaggio', 'importo': '9'})
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'promo', 'importo': '10,50',
                               'nota': 'promo pioggia'})
        r = ap.riepilogo(self.chiusura)
        self.assertEqual((r['valore_residuo'], r['valore_residuo_listino'], r['rettifica_residuo'],
                          r['n_rettificati']), (Decimal('25.50'), Decimal('45.00'), Decimal('19.50'), 2))
        self.assertEqual(RettificaResiduo.objects.get(transazione=omaggio).importo, Decimal('0.00'))
        self.assertEqual(RettificaResiduo.objects.get(transazione=promo).nota, 'promo pioggia')

        # di nuovo a listino
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'listino'})
        self.assertEqual(ap.riepilogo(self.chiusura)['valore_residuo'], Decimal('15.00') + Decimal('15.00'))
        # importo negativo rifiutato
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'promo', 'importo': '-3'})
        self.assertFalse(RettificaResiduo.objects.filter(transazione=promo).exists())

        # prelavaggio: incassato piu' del listino (di base listino + 4 euro)
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'prelavaggio', 'importo': ''})
        self.assertEqual(RettificaResiduo.objects.get(transazione=promo).importo, Decimal('19.00'))
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'prelavaggio', 'importo': '20'})
        self.assertEqual(RettificaResiduo.objects.get(transazione=promo).get_motivo_display(), 'Prelavaggio')
        self.assertEqual(ap.riepilogo(self.chiusura)['valore_residuo'], Decimal('35.00'))
        self.client.post(url, {**base, 'transazione_id': promo.pk, 'motivo': 'listino'})

        # in quadratura conta l'incassato
        from apps.finanze.views import _quadratura_giornata
        q, _, _ = _quadratura_giornata(self.chiusura.data, None, Decimal('0'), Decimal('0'),
                                       ap.riepilogo(self.chiusura)['valore_residuo'])
        self.assertEqual(q['residuo_operatori'], Decimal('30.00'))

    def test_ordini_senza_programmi_si_abbinano_a_mano(self):
        cat = self.completo.categoria
        sottoscocca = ServizioProdotto.objects.create(titolo='Lavaggio sottoscocca', categoria=cat,
                                                      prezzo=Decimal('15'), descrizione='')
        prodotto = ServizioProdotto.objects.create(titolo='Profumo', categoria=cat, prezzo=Decimal('5'),
                                                   descrizione='', tipo='prodotto')
        item = self._item(sottoscocca, completato='12:00')
        ItemOrdine.objects.create(ordine=item.ordine, servizio_prodotto=sottoscocca, quantita=1,
                                  prezzo_unitario=Decimal('15'))         # stesso ordine: una riga sola
        solo_prodotto = Ordine.objects.create(totale=Decimal('5'), totale_finale=Decimal('5'))
        Ordine.objects.filter(pk=solo_prodotto.pk).update(data_ora=ora('13:00'))
        ItemOrdine.objects.create(ordine=solo_prodotto, servizio_prodotto=prodotto, quantita=1,
                                  prezzo_unitario=Decimal('5'))
        tx = self._tx('11:40', 3)

        r = ap.riepilogo(self.chiusura)
        manuali = [x for x in r['righe'] if x['manuale']]
        self.assertEqual(len(manuali), 1)                                   # niente ordine di soli prodotti
        self.assertEqual((r['n_manuali'], r['n_manuali_aperti'], r['n_scoperti'], r['n_proposte']), (1, 1, 0, 0))
        self.assertEqual([t.pk for t in manuali[0]['compatibili']], [tx.pk])  # qualsiasi programma
        self.assertIn(tx.pk, [x['transazione'].pk for x in r['residuo']])       # non associato: resta residuo

        ok, _ = ap.abbina_manuale(self.chiusura, manuali[0]['item'].pk, tx.pk)
        self.assertTrue(ok)
        r = ap.riepilogo(self.chiusura)
        self.assertNotIn(tx.pk, [x['transazione'].pk for x in r['residuo']])
        self.assertEqual(r['n_manuali_aperti'], 0)
        altra = self._tx('11:50', 4)
        ok, _ = ap.abbina_manuale(self.chiusura, manuali[0]['item'].pk, altra.pk)
        self.assertFalse(ok)                                                # una sola transazione

    def test_rimuovi_fuori_finestra_e_scarti(self):
        # finestra chiusa alle 18:00: lavaggio delle 19:45 abbinato alla
        # transazione delle 19:26 (fuori finestra, stesso giorno)
        item = self._item(self.a_mano, completato='19:45')
        tx = self._tx('19:26', 5)
        self.assertEqual([p['transazione'].pk for p in ap.proponi(self.chiusura)], [tx.pk])
        ap.conferma_proposte(self.chiusura)
        a = AbbinamentoPortale.objects.get()
        self.assertTrue(ap.rimuovi(self.chiusura, a.pk))
        self.assertFalse(AbbinamentoPortale.objects.exists())
        self.assertEqual(ap.proponi(self.chiusura), [])                     # non riproposta
        # abbinata di nuovo a mano: lo scarto sparisce
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, tx.pk)
        self.assertTrue(ok)
        from apps.finanze.models import AbbinamentoScartato
        self.assertFalse(AbbinamentoScartato.objects.exists())

    def test_rifiuta_proposta(self):
        item = self._item(self.completo, completato='10:30')
        vicina = self._tx('10:00', 4)
        altra = self._tx('09:40', 4)
        self.assertEqual([p['transazione'].pk for p in ap.proponi(self.chiusura)], [vicina.pk])
        self.assertTrue(ap.rifiuta(self.chiusura, item.pk, vicina.pk))
        self.assertEqual([p['transazione'].pk for p in ap.proponi(self.chiusura)], [altra.pk])
        user = utente_titolare('op2', 'op2@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        r = self.client.post(reverse('finanze:azione_abbinamento_portali'),
                             {'data': '2026-10-06', 'azione': 'rifiuta', 'item_id': item.pk,
                              'transazione_id': altra.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ap.proponi(self.chiusura), [])

    def test_badge_fuori_standard(self):
        a_mano = self._item(self.a_mano, completato='10:30')             # standard P5
        completo = self._item(self.completo, completato='12:30')         # standard P8/P9/P7/P4
        p4 = self._tx('10:05', 4)
        p8 = self._tx('12:00', 8)
        ap.abbina_manuale(self.chiusura, a_mano.pk, p4.pk)
        ap.abbina_manuale(self.chiusura, completo.pk, p8.pk)
        r = ap.riepilogo(self.chiusura)
        per_item = {x['item'].pk: x['fuori_standard'] for x in r['righe']}
        self.assertEqual(len(per_item[a_mano.pk]), 1)
        self.assertEqual(per_item[completo.pk], [])
        self.assertEqual(r['n_fuori_standard'], 1)

    def test_chiudi_senza_portale(self):
        item = self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        self.assertEqual((r['n_proposte'], r['n_scoperti']), (1, 0))

        admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        self.client.force_login(admin)
        url = reverse('finanze:azione_abbinamento_portali')
        report = reverse('finanze:report_giornata')
        self.assertContains(self.client.get(report, {'data': '2026-10-06'}), 'Chiudi senza portale')
        self.client.post(url, {'data': '2026-10-06', 'azione': 'senza_portale', 'item_id': item.pk})
        pagina = self.client.get(report, {'data': '2026-10-06'})
        self.assertContains(pagina, 'Chiuso senza portale')
        self.assertNotContains(pagina, 'Chiudi senza portale')
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        riga = r['righe'][0]
        self.assertEqual((r['n_proposte'], r['n_scoperti'], r['n_senza_portale']), (0, 0, 1))
        self.assertEqual((riga['compatibili'], riga['slot_liberi'], riga['senza_portale'].operatore), ([], 0, admin))
        self.assertEqual(len(r['residuo']), 1)                  # la transazione resta pagata agli operatori
        self.assertEqual(ap.conferma_proposte(self.chiusura), 0)

        self.client.post(url, {'data': '2026-10-06', 'azione': 'riapri', 'item_id': item.pk})
        r = ap.riepilogo(self.chiusura, con_proposte=True)
        self.assertEqual((r['n_proposte'], r['n_senza_portale']), (1, 0))

    def test_report_proposte_solo_col_tasto(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.finanze.views import report_giornata
        self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)
        admin = utente_titolare('tit', 'tit@x.it', 'x', is_staff=True)

        def pagina(**extra):
            req = RequestFactory().get('/', {'data': '2026-10-06', **extra})
            req.user = admin
            req.session = {}
            req._messages = FallbackStorage(req)
            return report_giornata(req).content.decode()

        html = pagina()
        self.assertIn('Proponi abbinamenti', html)
        self.assertNotIn('Conferma 1 proposta', html)
        html = pagina(proposte='1')
        self.assertIn('Conferma 1 proposta', html)
        self.assertIn('Nascondi proposte', html)
        self.assertFalse(AbbinamentoPortale.objects.exists())       # mostrare non salva nulla
        # scartando una proposta si resta con le proposte visibili
        self.client.force_login(admin)
        r = self.client.post(reverse('finanze:azione_abbinamento_portali'),
                             {'data': '2026-10-06', 'azione': 'rifiuta', 'item_id': 0,
                              'transazione_id': 0, 'proposte': '1'})
        self.assertIn('proposte=1', r['Location'])

    def test_abbina_senza_ricaricare(self):
        item = self._item(self.a_mano, completato='10:30')
        p5 = self._tx('10:05', 5)
        self.client.force_login(utente_titolare('tit2', 'tit2@x.it', 'x', is_staff=True))
        r = self.client.post(reverse('finanze:azione_abbinamento_portali'),
                             {'data': '2026-10-06', 'azione': 'abbina', 'item_id': item.pk,
                              'transazione_id': p5.pk}, HTTP_X_REQUESTED_WITH='fetch')
        self.assertTrue(r.json()['ok'])
        self.assertTrue(AbbinamentoPortale.objects.filter(item=item, transazione=p5).exists())
        riga = ap.riepilogo(self.chiusura)['righe'][0]
        self.assertEqual((riga['slot_liberi'], riga['compatibili']), (0, []))

    def test_residuo_segna_vista(self):
        from apps.finanze.models import ResiduoVisto
        tx = self._tx('15:00', 1)
        self.client.force_login(utente_titolare('tit3', 'tit3@x.it', 'x', is_staff=True))
        url = reverse('finanze:azione_abbinamento_portali')
        base = {'data': '2026-10-06', 'azione': 'visto', 'transazione_id': tx.pk}
        r = self.client.post(url, {**base, 'visto': '1'}, HTTP_X_REQUESTED_WITH='fetch')
        self.assertEqual(r.json(), {'ok': True, 'visto': True})
        r = ap.riepilogo(self.chiusura)
        self.assertEqual((r['n_visti'], r['residuo'][0]['visto'].transazione_id), (1, tx.pk))
        self.assertEqual(r['valore_residuo'], Decimal('15.00'))          # non cambia i conti
        self.client.post(url, {**base, 'visto': '0'}, HTTP_X_REQUESTED_WITH='fetch')
        self.assertFalse(ResiduoVisto.objects.exists())
        self.assertIsNone(ap.riepilogo(self.chiusura)['residuo'][0]['visto'])

    def test_conferma_idempotente(self):
        self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)
        self.assertEqual(ap.conferma_proposte(self.chiusura), 1)
        self.assertEqual(ap.conferma_proposte(self.chiusura), 0)
        self.assertEqual(AbbinamentoPortale.objects.count(), 1)
        r = ap.riepilogo(self.chiusura)
        self.assertEqual((r['n_confermati'], r['n_proposte']), (1, 0))

    def test_abbina_manuale_validazioni(self):
        item = self._item(self.a_mano, completato='10:30')
        p4 = self._tx('10:05', 4)
        p5 = self._tx('11:30', 5)
        # a mano si sceglie anche un programma fuori standard (P4 su un
        # completo a mano, standard P5): l'elenco e' in ordine cronologico
        riga = next(r for r in ap.riepilogo(self.chiusura)['righe'] if r['item'].pk == item.pk)
        self.assertEqual([t.pk for t in riga['compatibili']], [p4.pk, p5.pk])
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, p4.pk)
        self.assertTrue(ok)
        item2 = self._item(self.a_mano, completato='12:10')
        ok, _ = ap.abbina_manuale(self.chiusura, item2.pk, p4.pk)
        self.assertFalse(ok)                    # transazione gia' usata
        # le proposte automatiche restano sui programmi standard
        self.assertEqual([p['transazione'].pk for p in ap.proponi(self.chiusura)], [p5.pk])

    def test_endpoint_conferma_e_rimuovi(self):
        user = utente_titolare('op', 'op@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)
        url = reverse('finanze:azione_abbinamento_portali')
        r = self.client.post(url, {'data': '2026-10-06', 'azione': 'conferma'})
        self.assertEqual(r.status_code, 302)
        abb = AbbinamentoPortale.objects.get()
        r = self.client.post(url, {'data': '2026-10-06', 'azione': 'rimuovi',
                                   'abbinamento_id': abb.pk})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(AbbinamentoPortale.objects.exists())

    def test_finestra_predefinita_senza_chiusura(self):
        # Senza chiusura salvata: 19:30 del giorno prima -> 19:30 del giorno
        from apps.finanze.views import _contesto_lavaggi_portali
        self.chiusura.delete()
        self._tx('10:05', 4, origine='contanti')
        TransazionePortale.objects.create(       # 06/10 20:00: giornata dopo
            portale='A', numero=50000, orario=ora('20:00'), programma=4, origine='contanti')
        ctx = _contesto_lavaggi_portali(ora('10:00').date())
        self.assertFalse(ctx['portali_chiusura_salvata'])
        self.assertEqual(ctx['portali_n_transazioni'], 1)
        self.assertEqual(ctx['washcycles_self']['n_contanti'], 1)
        self.assertEqual(timezone.localtime(ctx['portali_chiusura'].periodo_da).strftime('%d/%m %H:%M'),
                         '05/10 19:30')
        self.assertFalse(ChiusuraPortali.objects.exists())

    def test_abbinare_salva_la_finestra_predefinita(self):
        user = utente_titolare('op', 'op@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        self.chiusura.delete()
        self._item(self.completo, completato='10:30')
        self._tx('10:05', 4)
        self.client.post(reverse('finanze:azione_abbinamento_portali'),
                         {'data': '2026-10-06', 'azione': 'conferma'})
        c = ChiusuraPortali.objects.get()
        self.assertEqual((c.operatore, timezone.localtime(c.periodo_a).strftime('%d/%m %H:%M')),
                         (user, '06/10 19:30'))
        self.assertEqual(AbbinamentoPortale.objects.count(), 1)

    def test_chiusura_al_secondo(self):
        user = utente_titolare('op', 'op@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        url = reverse('finanze:imposta_chiusura_portali')
        self.client.post(url, {'data': '2026-10-06', 'periodo_da': '2026-10-05T19:30',
                               'periodo_a': '2026-10-06T17:48:05'})
        c = ChiusuraPortali.objects.get(data=ora('10:00').date())
        self.assertEqual(timezone.localtime(c.periodo_a).strftime('%H:%M:%S'), '17:48:05')
        self.assertEqual(timezone.localtime(c.periodo_da).strftime('%H:%M:%S'), '19:30:00')

    def test_interrotti_nel_report(self):
        from apps.finanze.views import _contesto_lavaggi_portali
        self._tx('10:05', 4)
        fermo = self._tx('11:00', 4)
        TransazionePortale.tutte.filter(pk=fermo.pk).update(eseguita=False)
        ctx = _contesto_lavaggi_portali(self.chiusura.data)
        azzurro = ctx['lavaggi_portali'][0]
        self.assertEqual((azzurro['tot']['richieste'], [t.pk for t in azzurro['interrotte']]), (1, [fermo.pk]))
        self.assertEqual([r['transazione'].numero for r in ctx['abbinamento']['residuo']], [fermo.numero - 1])
        stato = {t.pk: t.eseguita for t in ctx['transazioni_finestra']}
        self.assertEqual(stato[fermo.pk], False)                # elencata, segnata interrotta

    def test_lavaggi_ai_bordi(self):
        from apps.finanze.views import _contesto_lavaggi_portali
        # finestra 05/10 19:00 -> 06/10 18:00
        self._tx('17:50', 4)
        self._tx('18:05', 8)
        self._tx('19:00', 4)        # oltre i 20 minuti: non elencato
        ctx = _contesto_lavaggi_portali(self.chiusura.data)
        fine = ctx['portali_bordi'][1]
        self.assertEqual(fine['etichetta'], 'Fine')
        self.assertEqual([(timezone.localtime(r['t'].orario).strftime('%H:%M'), r['dentro'])
                          for r in fine['righe']], [('17:50', True), ('18:05', False)])

    def _scontrino(self, nome, wash_cycles, vendita=0):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        cassa, _ = Cassa.objects.get_or_create(nome=nome, defaults={'tipo': 'automatica'})
        return ChiusuraCassaAutomatica.objects.create(
            cassa=cassa, data=self.chiusura.data, wash_cycles=wash_cycles,
            vendita_contante=Decimal(vendita))

    def _tx_portale(self, portale, alle, programma=4, origine='unita'):
        self.n_tx += 1
        return TransazionePortale.objects.create(
            portale=portale, numero=20000 + self.n_tx, orario=ora(alle),
            programma=programma, origine=origine)

    def test_finestra_blu_separata(self):
        # Blu chiuso alle 18:20: il suo lavaggio delle 18:10 conta, quello
        # dell'Azzurro alla stessa ora no (fine generale 18:00)
        self.chiusura.periodo_a_blu = ora('18:20')
        self.chiusura.save()
        a = self._tx_portale('A', '18:10')
        b = self._tx_portale('B', '18:10')
        self.assertFalse(self.chiusura.contiene(a))
        self.assertTrue(self.chiusura.contiene(b))
        self.assertEqual([t.pk for t in ap.transazioni_libere(self.chiusura)], [b.pk])

    def test_allinea_agli_scontrini(self):
        # Come il 17/09: Azzurro torna con la fine generale, il Blu solo
        # spostando la sua fine dopo il lavaggio delle 18:15
        from apps.finanze.services import allinea_scontrini as al
        for alle in ('09:00', '12:00', '17:50'):
            self._tx_portale('A', alle)
        self._tx_portale('A', '18:05')                  # Azzurro: dopo la chiusura
        for alle in ('10:00', '17:55', '18:15'):
            self._tx_portale('B', alle)
        self._tx_portale('B', '18:40')
        self._scontrino('Portale Azzurro', 3)
        self._scontrino('Portale Blu', 3)
        casse = al.chiusure_casse_portali(self.chiusura.data)
        self.assertEqual(al.washcycles_scontrini(self.chiusura.data), {'A': 3, 'B': 3})
        esito = al.allinea(self.chiusura, casse)
        self.assertEqual(esito['A']['fine'], ora('18:00'))          # invariata
        self.assertEqual(esito['B']['fine'], ora('18:15'))          # primo orario utile
        self.assertEqual(esito['B']['tra'], (ora('18:15'), ora('18:40') - timedelta(seconds=1)))

    def test_allinea_priorita_ai_contanti(self):
        # Scontrino: 3 cicli e 10 euro. Con 3 lavaggi i contanti sarebbero
        # 18 (P4 + P3): vince la fine che fa tornare i 10 euro (2 lavaggi)
        from apps.finanze.services import allinea_scontrini as al
        self._tx_portale('A', '16:30', programma=3, origine='contanti')     # 10 euro
        self._tx_portale('A', '17:00')                                     # unita'
        self._tx_portale('A', '17:40', programma=4, origine='contanti')     # 8 euro
        cassa = self._scontrino('Portale Azzurro', 3, vendita=10)
        v = al.allinea(self.chiusura, {'A': cassa, 'B': None})['A']
        self.assertEqual((v['conteggio'], v['contanti'], v['scarto'], v['scarto_contanti']),
                         (2, Decimal('10.00'), -1, Decimal('0.00')))
        self.assertEqual(v['tra'], (ora('17:00'), ora('17:40') - timedelta(seconds=1)))

    def test_allinea_inizio_dalla_fine_del_giorno_prima(self):
        from apps.finanze.services import allinea_scontrini as al
        ChiusuraPortali.objects.create(
            data=self.chiusura.data - timedelta(days=1),
            periodo_da=timezone.make_aware(datetime(2026, 10, 4, 19, 0)),
            periodo_a=timezone.make_aware(datetime(2026, 10, 5, 18, 30)))   # buco 18:30-19:00
        cassa = self._scontrino('Portale Azzurro', 0)
        v = al.allinea(self.chiusura, {'A': cassa, 'B': None})['A']
        self.assertEqual(v['da'], timezone.make_aware(datetime(2026, 10, 5, 18, 30)))
        self.assertNotEqual(v['da'], v['da_attuale'])

    def test_allinea_segnala_lavaggi_mancanti(self):
        from apps.finanze.services import allinea_scontrini as al
        self._tx_portale('B', '10:00')
        cassa = self._scontrino('Portale Blu', 5)
        esito = al.allinea(self.chiusura, {'A': None, 'B': cassa})
        self.assertIn('solo 1 lavaggi', esito['B']['errore'])
        self.assertNotIn('A', esito)

    def test_allinea_resta_nella_sera(self):
        # Lo scontrino conta un lavaggio in piu': niente fine al mattino
        # dopo, ma la sera con lo scarto segnalato
        from apps.finanze.services import allinea_scontrini as al
        self._tx_portale('B', '10:00')
        self._tx_portale('B', '17:30')
        TransazionePortale.objects.create(portale='B', numero=29999, programma=4, origine='unita',
                                          orario=ora('08:00') + timedelta(days=1))
        cassa = self._scontrino('Portale Blu', 3)
        esito = al.allinea(self.chiusura, {'A': None, 'B': cassa})['B']
        self.assertEqual((esito['conteggio'], esito['scarto'], esito['errore']), (2, -1, ''))
        self.assertEqual(esito['fine'], ora('18:00'))

    def test_allinea_periodo_insieme(self):
        # 06/10: 2 cicli e 10 euro; 07/10: 3 cicli e 16 euro. Il P4 in
        # contanti delle 18:30 del 06 va sul 07: fine del 06 tra 17:00 e 18:30
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.finanze.services import allinea_scontrini as al
        giorno, dopo = self.chiusura.data, self.chiusura.data + timedelta(days=1)
        self._tx_portale('A', '10:00', programma=3, origine='contanti')
        self._tx_portale('A', '17:00')
        self._tx_portale('A', '18:30', programma=4, origine='contanti')
        for numero, alle, prog, orig in ((21001, '09:00', 4, 'unita'), (21002, '17:00', 4, 'contanti')):
            TransazionePortale.objects.create(portale='A', numero=numero, programma=prog,
                                              origine=orig, orario=ora(alle) + timedelta(days=1))
        azzurro, _ = Cassa.objects.get_or_create(nome='Portale Azzurro', defaults={'tipo': 'automatica'})
        ChiusuraCassaAutomatica.objects.create(cassa=azzurro, data=giorno, wash_cycles=2,
                                               vendita_contante=Decimal('10'))
        ChiusuraCassaAutomatica.objects.create(cassa=azzurro, data=dopo, wash_cycles=3,
                                               vendita_contante=Decimal('16'))
        esito = al.salva_periodo(giorno, dopo)
        self.assertEqual([(esito[g]['A']['conteggio'], esito[g]['A']['contanti']) for g in (giorno, dopo)],
                         [(2, Decimal('10.00')), (3, Decimal('16.00'))])
        c6 = ChiusuraPortali.objects.get(data=giorno)
        c7 = ChiusuraPortali.objects.get(data=dopo)
        self.assertEqual(c6.periodo_a, ora('18:00'))                 # resta l'orario attuale
        self.assertEqual(c7.periodo_da, c6.periodo_a)                 # continuita'
        self.assertTrue(ora('17:00') + timedelta(days=1) <= c7.periodo_a)

    def test_allinea_periodo_giorno_senza_chiusura_di_cassa(self):
        # 06/10 senza scontrino, 07/10 con scontrino che conta anche il 06:
        # il 06 resta vuoto e tutto va sul 07
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.finanze.services import allinea_scontrini as al
        giorno, dopo = self.chiusura.data, self.chiusura.data + timedelta(days=1)
        self._tx_portale('A', '10:00', programma=3, origine='contanti')
        TransazionePortale.objects.create(portale='A', numero=21001, programma=4, origine='contanti',
                                          orario=ora('11:00') + timedelta(days=1))
        azzurro, _ = Cassa.objects.get_or_create(nome='Portale Azzurro', defaults={'tipo': 'automatica'})
        ChiusuraCassaAutomatica.objects.create(cassa=azzurro, data=dopo, wash_cycles=2,
                                               vendita_contante=Decimal('18'))
        esito = al.salva_periodo(giorno, dopo)
        self.assertIsNone(esito[giorno]['A']['fine'])
        self.assertEqual((esito[dopo]['A']['conteggio'], esito[dopo]['A']['contanti']),
                         (2, Decimal('18.00')))
        c6 = ChiusuraPortali.objects.get(data=giorno)
        self.assertEqual(c6.periodo_a, c6.periodo_da)

    def test_allinea_periodo_inizio_libero(self):
        # Il giorno prima non ha chiusura salvata: l'inizio si sposta prima
        # del P4 in contanti delle 18:00 del 05/10 che lo scontrino conta
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.finanze.services import allinea_scontrini as al
        TransazionePortale.objects.create(portale='A', numero=21001, programma=4, origine='contanti',
                                          orario=ora('18:00') - timedelta(days=1))
        self._tx_portale('A', '10:00')
        azzurro, _ = Cassa.objects.get_or_create(nome='Portale Azzurro', defaults={'tipo': 'automatica'})
        ChiusuraCassaAutomatica.objects.create(cassa=azzurro, data=self.chiusura.data, wash_cycles=2,
                                               vendita_contante=Decimal('8'))
        inizi, esito = al.allinea_periodo(self.chiusura.data, self.chiusura.data)
        self.assertLess(inizi['A'], ora('18:00') - timedelta(days=1))
        self.assertEqual((esito[self.chiusura.data]['A']['conteggio'],
                          esito[self.chiusura.data]['A']['contanti']), (2, Decimal('8.00')))

    def test_salva_orari_blu(self):
        user = utente_titolare('op', 'op@x.it', 'x', is_staff=True)
        self.client.force_login(user)
        url = reverse('finanze:imposta_chiusura_portali')
        self.client.post(url, {'data': '2026-10-06', 'periodo_da': '2026-10-05T19:00',
                               'periodo_a': '2026-10-06T18:00',
                               'periodo_da_blu': '2026-10-05T19:00',      # uguale: ignorato
                               'periodo_a_blu': '2026-10-06T18:20:30'})
        c = ChiusuraPortali.objects.get(data=ora('10:00').date())
        self.assertIsNone(c.periodo_da_blu)
        self.assertEqual(timezone.localtime(c.periodo_a_blu).strftime('%H:%M:%S'), '18:20:30')
        # il giorno dopo il Blu riparte dalla sua fine
        from apps.finanze.views import _chiusura_portali
        dopo, salvata = _chiusura_portali(c.data + timedelta(days=1))
        self.assertFalse(salvata)
        self.assertEqual(dopo.finestra('B')[0], c.periodo_a_blu)
        self.assertEqual(dopo.finestra('A')[0], c.periodo_a)

    def test_programmi_portale_ordinati(self):
        s = ServizioProdotto(programmi_portale=' 8, 9,x,12,7,8,4 ')
        self.assertEqual(s.programmi_portale_ordinati, [8, 9, 7, 4])
        self.assertEqual(s.lista_programmi_portale, {4, 7, 8, 9})


class ImportWashtecTest(TestCase):
    """Classificazione delle righe grezze del bookmarklet WashTec."""

    def setUp(self):
        # Archivio esistente: A ~13300, B ~13970
        for portale, numero in (('A', 13300), ('B', 13970)):
            TransazionePortale.objects.create(
                portale=portale, numero=numero, orario=ora('08:00'),
                programma=4, origine='unita')

    def riga(self, numero, metodo, pagato='0,00 EUR', prog='4',
             manut='No', eseguito='Sì', orario='07/10/26 10:00:00'):
        return [str(numero), orario, prog, metodo, pagato, manut, eseguito]

    def test_classificazione_completa(self):
        from apps.finanze.services import import_washtec as iw
        righe = [
            self.riga(13301, 'In contanti'),                     # A, unita
            self.riga(13971, 'Unità operativa parallela'),       # B, contanti
            self.riga(15300, 'Gettone', '2,00 EUR', '1'),        # JetWash
            self.riga(8550, '', '6,00 EUR', '1'),                # JetWash
            self.riga(13302, 'In contanti', manut='Sì'),         # esclusa
            self.riga(13303, 'Gettone', '0,00 EUR'),             # anomalia
            self.riga(13650, 'In contanti'),                     # ambiguo
        ]
        esito = iw.classifica(righe)
        trans = {t['numero']: (t['portale'], t['origine']) for t in esito['transazioni']}
        self.assertEqual(trans, {13301: ('A', 'unita'), 13971: ('B', 'contanti')})
        self.assertEqual(esito['jetwash'], 2)
        self.assertEqual(len(esito['escluse']), 1)
        self.assertEqual(len(esito['anomalie']), 2)

    def test_buchi_archivio(self):
        # A 13300 (setUp) e 13303: mancano 13301-13302
        from apps.finanze.services import import_washtec as iw
        TransazionePortale.objects.create(portale='A', numero=13303, orario=ora('12:00'),
                                          programma=4, origine='unita')
        buchi = iw.buchi_archivio(ora('07:00'), ora('20:00'))
        self.assertEqual([(b['portale'], b['da_numero'], b['a_numero'], b['quanti']) for b in buchi],
                         [('A', 13301, 13302, 2)])
        self.assertEqual(iw.buchi_archivio(ora('13:00'), ora('20:00')), [])   # fuori finestra

    def test_lavaggi_interrotti(self):
        # eseguito = No: salvato come interrotto, non conta e non fa buco
        from apps.finanze.services import import_washtec as iw
        righe = [self.riga(13301, 'In contanti', orario='06/10/26 10:00:00'),
                 self.riga(13302, 'In contanti', eseguito='No', orario='06/10/26 10:05:00'),
                 self.riga(13303, 'In contanti', orario='06/10/26 10:10:00')]
        esito = iw.importa(righe, conferma=True)
        self.assertEqual((esito['nuove'], len(esito['interrotte']), esito['escluse']), (3, 1, []))
        self.assertEqual(esito['per_giorno'], [{'data': '2026-10-06', 'A': 2, 'B': 0}])
        interrotta = TransazionePortale.tutte.get(numero=13302)
        self.assertFalse(interrotta.eseguita)
        self.assertFalse(TransazionePortale.objects.filter(numero=13302).exists())
        self.assertEqual(iw.buchi_archivio(ora('07:00'), ora('20:00')), [])
        # reimportare non la duplica
        self.assertEqual(iw.importa(righe, conferma=True)['nuove'], 0)

    def test_import_idempotente(self):
        from apps.finanze.services import import_washtec as iw
        righe = [self.riga(13301, 'In contanti'), self.riga(13302, 'In contanti')]
        ante = iw.importa(righe, conferma=False)
        self.assertEqual((ante['nuove'], ante['importato']), (2, False))
        self.assertEqual(TransazionePortale.objects.count(), 2)
        iw.importa(righe, conferma=True)
        self.assertEqual(TransazionePortale.objects.count(), 4)
        ancora = iw.importa(righe, conferma=True)
        self.assertEqual((ancora['nuove'], ancora['gia_presenti']), (0, 2))
        self.assertEqual(TransazionePortale.objects.count(), 4)

    def test_contatori_che_avanzano(self):
        # Una serie lunga in crescita resta sullo stesso portale
        from apps.finanze.services import import_washtec as iw
        righe = [self.riga(n, 'In contanti') for n in range(13301, 13340)]
        esito = iw.classifica(righe)
        self.assertTrue(all(t['portale'] == 'A' for t in esito['transazioni']))
        self.assertEqual(len(esito['transazioni']), 39)

    def serie_settembre(self, giorni, a0=12421, b0=13121, per_giorno=20):
        """Due contatori intrecciati come a settembre 2026: B usava i
        numeri che a ottobre usa A."""
        righe, a, b = [], a0, b0
        for g in giorni:
            for i in range(per_giorno):
                orario = f'{g:02d}/09/26 {8 + i // 4:02d}:{(i % 4) * 15:02d}:00'
                righe.append(self.riga(a, 'In contanti', orario=orario))
                righe.append(self.riga(b, 'In contanti', orario=orario.replace(':00:00', ':05:00')
                                       .replace(':15:00', ':20:00').replace(':30:00', ':35:00')
                                       .replace(':45:00', ':50:00')))
                a += 1
                b += 1
        return righe

    def portali(self, esito):
        return {t['numero']: t['portale'] for t in esito['transazioni']}

    def test_mese_precedente_contiguo(self):
        # Le due catene arrivano fino ai numeri gia' in archivio
        from apps.finanze.services import import_washtec as iw
        TransazionePortale.objects.all().delete()
        for portale, numero in (('A', 13300), ('B', 14000)):
            TransazionePortale.objects.create(
                portale=portale, numero=numero, orario=ora('08:00'),
                programma=4, origine='unita')
        righe = self.serie_settembre(range(1, 31), a0=13300 - 600, b0=14000 - 600)
        esito = iw.classifica(righe)
        p = self.portali(esito)
        self.assertEqual(esito['anomalie'], [])
        self.assertEqual(p[12700], 'A')
        self.assertEqual(p[13400], 'B')   # numero che A usera' dopo il 6/10
        self.assertEqual((p[13299], p[13999]), ('A', 'B'))
        self.assertEqual(len(p), 1200)

    def test_pochi_giorni_lontani(self):
        # 1-5/09 senza continuita' con l'archivio: B si riconosce perche'
        # i suoi numeri superano quelli di A gia' noti a ottobre
        from apps.finanze.services import import_washtec as iw
        esito = iw.classifica(self.serie_settembre(range(1, 6), b0=13121, per_giorno=40))
        p = self.portali(esito)
        self.assertEqual(esito['anomalie'], [])
        self.assertEqual((p[12421], p[13121], p[13320]), ('A', 'B', 'B'))

    def test_un_giorno_lontano_spareggio(self):
        # Entrambe le catene compatibili con entrambi i portali: vale
        # l'ordine dei contatori in archivio (A piu' basso di B)
        from apps.finanze.services import import_washtec as iw
        esito = iw.classifica(self.serie_settembre([1], b0=13121))
        p = self.portali(esito)
        self.assertEqual(esito['anomalie'], [])
        self.assertEqual((p[12421], p[13121]), ('A', 'B'))

    def test_gia_presente_riconosciuto_da_numero_e_orario(self):
        from apps.finanze.services import import_washtec as iw
        righe = [self.riga(13300, 'In contanti', orario='06/10/26 08:00:00'),
                 self.riga(13970, 'In contanti', orario='06/10/26 08:00:00')]
        esito = iw.importa(righe)
        self.assertEqual((esito['nuove'], esito['gia_presenti']), (0, 2))


class SpeseCassaTest(TestCase):
    """Spese pagate coi contanti della cassa dal report giornata."""

    def setUp(self):
        from apps.finanze.models import SpesaCassa
        self.SpesaCassa = SpesaCassa
        self.op = utente_titolare('op_spese', password='x', is_staff=True)
        self.altro = utente_titolare('op_altro', password='x', is_staff=True)
        self.url = reverse('finanze:azione_spese_cassa')

    def aggiungi(self, **extra):
        dati = {'data': '2026-10-06', 'azione': 'aggiungi', 'descrizione': 'Panni microfibra',
                'categoria': 'prodotti', 'importo': '12,50', 'riferimento': 'sc. 45'}
        dati.update(extra)
        return self.client.post(self.url, dati)

    def test_aggiungi_spesa(self):
        self.client.force_login(self.op)
        r = self.aggiungi()
        self.assertEqual(r.status_code, 302)
        self.assertIn('#spese-cassa', r['Location'])
        s = self.SpesaCassa.objects.get()
        self.assertEqual((s.importo, s.categoria, s.operatore, str(s.data)),
                         (Decimal('12.50'), 'prodotti', self.op, '2026-10-06'))

    def test_dati_non_validi(self):
        self.client.force_login(self.op)
        self.aggiungi(importo='0')
        self.aggiungi(importo='abc')
        self.aggiungi(descrizione='  ')
        self.assertFalse(self.SpesaCassa.objects.exists())

    def test_elimina_solo_autore_o_admin(self):
        self.client.force_login(self.op)
        self.aggiungi()
        s = self.SpesaCassa.objects.get()
        self.client.force_login(self.altro)
        self.client.post(self.url, {'data': '2026-10-06', 'azione': 'elimina', 'spesa_id': s.pk})
        self.assertTrue(self.SpesaCassa.objects.exists())
        self.client.force_login(self.op)
        self.client.post(self.url, {'data': '2026-10-06', 'azione': 'elimina', 'spesa_id': s.pk})
        self.assertFalse(self.SpesaCassa.objects.exists())

    def test_non_staff_respinto(self):
        self.client.force_login(User.objects.create_user('cliente', password='x'))
        self.aggiungi()
        self.assertFalse(self.SpesaCassa.objects.exists())


class SelfServiceQuadraturaTest(TestCase):
    """Nella quadratura il self service atteso e' incasso totale - ricarica."""

    def test_incasso_meno_ricarica(self):
        from datetime import date

        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.finanze.views import _saldo_giornata
        giorno = date(2026, 10, 6)
        cassa = Cassa.objects.create(nome='Cambia gettoni', tipo='automatica')
        ChiusuraCassaAutomatica.objects.create(
            cassa=cassa, data=giorno, incasso_totale=Decimal('500'), incasso_ricarica=Decimal('100'),
            vendita_contante=Decimal('350'), vendita_non_contante=Decimal('20'))
        quadratura, _ = _saldo_giornata(giorno)
        self.assertEqual(quadratura['vendita_self_service'], Decimal('400'))
        self.assertEqual(quadratura['totale_teorico'], Decimal('400'))


class TicketRestoTest(TestCase):
    """Ticket di resto ripagati dalla cassa: si sommano al reale."""

    def test_ticket_in_quadratura(self):
        from datetime import date

        from apps.finanze.models import QuadraturaGiornaliera, TicketResto
        from apps.finanze.views import _saldo_giornata
        giorno = date(2026, 10, 6)
        QuadraturaGiornaliera.objects.create(data=giorno, contanti_totali=Decimal('100'))
        admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        self.client.force_login(admin)
        url = reverse('finanze:azione_ticket_resto')
        self.client.post(url, {'data': '2026-10-07', 'azione': 'aggiungi', 'data_pagamento': '2026-10-06',
                               'importo': '4,50', 'nota': 'ticket 123'})
        self.client.post(url, {'data': '2026-10-06', 'azione': 'aggiungi', 'importo': '2'})
        self.assertEqual(TicketResto.objects.filter(data=giorno).count(), 2)
        quadratura, _ = _saldo_giornata(giorno)
        self.assertEqual(quadratura['ticket_resto'], Decimal('6.50'))
        reale_base = QuadraturaGiornaliera.objects.get(data=giorno).totale_reale
        self.assertEqual(quadratura['totale_reale'], reale_base + Decimal('6.50'))
        # validazioni: importo zero o data futura
        self.client.post(url, {'data': '2026-10-06', 'azione': 'aggiungi', 'importo': '0'})
        self.client.post(url, {'data': '2026-10-06', 'azione': 'aggiungi', 'importo': '5',
                               'data_pagamento': '2099-01-01'})
        self.assertEqual(TicketResto.objects.count(), 2)
        # elimina
        t = TicketResto.objects.get(importo=Decimal('2'))
        self.client.post(url, {'data': '2026-10-06', 'azione': 'elimina', 'ticket_id': t.pk})
        self.assertEqual(TicketResto.objects.count(), 1)



class ChiusuraGiornataOperatoriTest(TestCase):
    """Conteggio e dichiarazioni degli operatori; conferma del titolare."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.oggi = timezone.localdate()
        self.operatore = User.objects.create_user('op', password='x')
        self.operatore.groups.add(Group.objects.get(name='operatore'))
        self.titolare = utente_titolare('tit', password='x')
        self.url = reverse('ordini:chiusura-giornata')

    def _post(self, user, **dati):
        self.client.force_login(user)
        return self.client.post(self.url, {'data': self.oggi.isoformat(), **dati})

    def test_operatore_dichiara_e_il_report_incrocia(self):
        from apps.finanze.models import (LavaggioExtra, PromoGiornata, QuadraturaGiornaliera, SpesaCassa,
                                         TicketResto)
        from apps.finanze.views import _quadratura_giornata
        self._post(self.titolare, azione='conteggio', contanti_totali='300', lettore_carte_servito='50',
                   fondo_cassa_iniziale='100')
        self._post(self.operatore, azione='aggiungi_ticket', importo='3')
        self._post(self.operatore, azione='aggiungi_spesa', importo='10', descrizione='Gasolio')
        self._post(self.operatore, azione='aggiungi_extra', importo='20', descrizione='Lavaggio Panda',
                   metodo='contanti')
        self._post(self.operatore, azione='aggiungi_promo', tipo='macchine', importo='4', descrizione='2 gettoni')
        self._post(self.operatore, azione='aggiungi_promo', tipo='operatori', importo='8', descrizione='omaggio')
        self.assertEqual(QuadraturaGiornaliera.objects.get(data=self.oggi).totale_reale, Decimal('250.00'))
        self.assertEqual((TicketResto.objects.count(), SpesaCassa.objects.count(), LavaggioExtra.objects.count(),
                          PromoGiornata.objects.count()), (1, 1, 1, 2))

        q, _, _ = _quadratura_giornata(self.oggi, None, Decimal('200'), Decimal('0'), Decimal('15'))
        # reale = 250 + spese 10 + ticket 3; atteso = 200 - 4 + 0 + 20; residuo = 15 - 8
        self.assertEqual(q['totale_reale'], Decimal('263.00'))
        self.assertEqual(q['totale_teorico'], Decimal('216.00'))
        self.assertEqual((q['residuo_operatori_lordo'], q['residuo_operatori']), (Decimal('15'), Decimal('7.00')))
        self.assertEqual(q['differenza'], Decimal('263.00') - Decimal('216.00') - Decimal('7.00'))

    def test_ricariche_operatori_e_confronto(self):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica, RicaricaCassa
        cassa = Cassa.objects.create(nome='Cambia gettoni', tipo='automatica')
        registratore = Cassa.objects.create(nome='Registratore', tipo='automatica', modalita_registratore=True)
        self._post(self.operatore, azione='aggiungi_ricarica', cassa=cassa.pk, importo='40', nota='monete')
        self._post(self.operatore, azione='aggiungi_ricarica', cassa=cassa.pk, importo='10')
        self._post(self.operatore, azione='aggiungi_ricarica', cassa=registratore.pk, importo='5')   # non ricaricabile
        self._post(self.operatore, azione='aggiungi_ricarica', importo='5')                          # senza cassa
        self.assertEqual(RicaricaCassa.objects.count(), 2)
        ChiusuraCassaAutomatica.objects.create(cassa=cassa, data=self.oggi, incasso_totale=Decimal('300'),
                                               incasso_ricarica=Decimal('40'))
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.finanze.views import report_giornata
        req = RequestFactory().get('/', {'data': self.oggi.isoformat()})
        req.user = self.titolare
        req.session = {}
        req._messages = FallbackStorage(req)
        pagina = report_giornata(req).content.decode()
        self.assertIn('Ricariche delle casse', pagina)
        self.assertIn('+&euro;10,00', pagina)                 # dichiarato 50, scontrino 40

    def test_ricariche_dal_report_giornata(self):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica, RicaricaCassa
        cassa = Cassa.objects.create(nome='Cambia gettoni', tipo='automatica')
        registratore = Cassa.objects.create(nome='Registratore', tipo='automatica', modalita_registratore=True)
        url = reverse('finanze:azione_ricarica_cassa')
        self.client.force_login(self.titolare)
        r = self.client.post(url, {'data': self.oggi.isoformat(), 'azione': 'aggiungi', 'cassa': cassa.pk,
                                   'importo': '25,50', 'nota': 'monete'})
        self.assertRedirects(r, f"{reverse('finanze:report_giornata')}?data={self.oggi.isoformat()}#ricariche",
                             fetch_redirect_response=False)
        self.client.post(url, {'data': self.oggi.isoformat(), 'azione': 'aggiungi', 'cassa': registratore.pk,
                               'importo': '5'})                                  # non ricaricabile
        ricarica = RicaricaCassa.objects.get()
        self.assertEqual((ricarica.cassa, ricarica.importo, ricarica.operatore), (cassa, Decimal('25.50'), self.titolare))
        # lo scontrino della cassa propone la ricarica dichiarata; la checklist la confronta
        ChiusuraCassaAutomatica.objects.create(cassa=cassa, data=self.oggi, incasso_totale=Decimal('100'),
                                               incasso_ricarica=Decimal('25.50'), note='rotolo cambiato')
        pagina = self.client.get(reverse('finanze:report_giornata'), {'data': self.oggi.isoformat()})
        passi = {p['titolo']: p for p in pagina.context['passi_chiusura']}
        self.assertEqual((passi['Ricariche']['stato'], passi['Ricariche']['dettaglio']), ('ok', 'tornano'))
        self.assertEqual(passi['Conteggio cassa']['stato'], 'todo')
        self.assertContains(pagina, 'data-copia="25.50" data-target="incasso_ricarica"')
        # aggiornare lo scontrino dal report non cancella le note
        self.assertContains(pagina, 'name="note" value="rotolo cambiato"')
        # l'operatore non elimina la ricarica del titolare
        self.client.force_login(self.operatore)
        self.client.post(url, {'data': self.oggi.isoformat(), 'azione': 'elimina', 'ricarica_id': ricarica.pk})
        self.assertTrue(RicaricaCassa.objects.exists())
        self.client.force_login(self.titolare)
        self.client.post(url, {'data': self.oggi.isoformat(), 'azione': 'elimina', 'ricarica_id': ricarica.pk})
        self.assertFalse(RicaricaCassa.objects.exists())

    def test_distinta_cartacea(self):
        from apps.finanze.models import SpesaCassa
        SpesaCassa.objects.create(data=self.oggi, descrizione='Gasolio', importo=Decimal('20'), riferimento='45')
        self._post(self.titolare, azione='conteggio', contanti_totali='0', fondo_cassa_iniziale='150')
        from django.test import RequestFactory

        from apps.finanze.views_chiusura import distinta_chiusura
        req = RequestFactory().get('/', {'data': self.oggi.isoformat()})
        req.user = self.operatore
        html = distinta_chiusura(req).content.decode()
        self.assertIn('<td class="small">Gasolio</td><td class="small">45</td>', html)
        self.assertIn('150,00', html)
        self.assertIn('CONTEGGIO CASSA E SALDO FINALE', html)
        self.assertIn('-130,00</td>', html)                     # totale reale
        self.assertNotIn('<div class="v">-130,00</div>', html)   # l'operatore non vede il saldo
        # il titolare la stampa con atteso e saldo: reale 0 - 150 + 20 = -130, atteso 0
        req = RequestFactory().get('/', {'data': self.oggi.isoformat()})
        req.user = self.titolare
        html = distinta_chiusura(req).content.decode()
        self.assertIn('-130,00</td>', html)
        self.assertIn('<div class="v">-130,00</div>', html)
        self.assertIn('Totale spese</td><td class="imp">20,00</td>', html)   # totale sul fronte
        req = RequestFactory().get('/', {'vuota': '1'})
        req.user = self.operatore
        vuota = distinta_chiusura(req).content.decode()
        self.assertNotIn('Gasolio', vuota)
        self.assertEqual(vuota.count('<tr class="totale">'), 6)          # 5 riquadri + contanti
        self.assertIn('<td class="t">0,50</td>', vuota)                  # conteggio per taglio

    def test_validazioni(self):
        from apps.finanze.models import LavaggioExtra, PromoGiornata
        self._post(self.operatore, azione='aggiungi_extra', importo='0', descrizione='x')
        self._post(self.operatore, azione='aggiungi_extra', importo='5', descrizione='')
        self._post(self.operatore, azione='aggiungi_promo', tipo='boh', importo='5', descrizione='x')
        self.assertFalse(LavaggioExtra.objects.exists() or PromoGiornata.objects.exists())

    def test_conferma_blocca_gli_operatori(self):
        from apps.finanze.models import LavaggioExtra, QuadraturaGiornaliera
        # il conteggio lo fa il titolare: quello dell'operatore non passa
        self._post(self.operatore, azione='conteggio', contanti_totali='50')
        self.assertFalse(QuadraturaGiornaliera.objects.exists())
        self._post(self.titolare, azione='conteggio', contanti_totali='100')
        self._post(self.operatore, azione='aggiungi_extra', importo='5', descrizione='Moto')
        extra = LavaggioExtra.objects.get()
        # un altro operatore non elimina le voci altrui
        altro = User.objects.create_user('op2', password='x')
        from django.contrib.auth.models import Group
        altro.groups.add(Group.objects.get(name='operatore'))
        self._post(altro, azione='elimina_extra', id=extra.pk)
        self.assertTrue(LavaggioExtra.objects.exists())

        self.client.force_login(self.titolare)
        self.client.post(reverse('finanze:azione_conferma_giornata'), {'data': self.oggi.isoformat(),
                                                                        'azione': 'conferma'})
        self.assertIsNotNone(QuadraturaGiornaliera.objects.get().confermata_il)
        self._post(self.operatore, azione='aggiungi_extra', importo='9', descrizione='Altro')
        self._post(self.operatore, azione='conteggio', contanti_totali='999')
        self.assertEqual((LavaggioExtra.objects.count(), QuadraturaGiornaliera.objects.get().contanti_totali),
                         (1, Decimal('100.00')))
        # il titolare modifica anche dopo la conferma, e puo' sbloccare
        self._post(self.titolare, azione='aggiungi_extra', importo='9', descrizione='Altro')
        self.assertEqual(LavaggioExtra.objects.count(), 2)
        self.client.post(reverse('finanze:azione_conferma_giornata'), {'data': self.oggi.isoformat(),
                                                                        'azione': 'sblocca'})
        self._post(self.operatore, azione='aggiungi_extra', importo='3', descrizione='Moto')
        self.assertEqual(LavaggioExtra.objects.count(), 3)
        self._post(self.titolare, azione='conteggio', contanti_totali='120')
        self.assertEqual(QuadraturaGiornaliera.objects.get().contanti_totali, Decimal('120.00'))

    def test_permesso_conteggio_per_utente(self):
        # il conteggio si concede o si toglie al singolo utente (Configurazione > Permessi)
        from apps.auth_system.models import PermessoUtente
        from apps.finanze.models import QuadraturaGiornaliera
        PermessoUtente.objects.create(user=self.operatore, sezione='conteggio_cassa', consenti=True)
        self.client.force_login(self.operatore)
        self.assertContains(self.client.get(self.url), 'name="contanti_totali"')
        self._post(self.operatore, azione='conteggio', contanti_totali='80')
        self.assertEqual(QuadraturaGiornaliera.objects.get().contanti_totali, Decimal('80.00'))
        # al titolare si puo' togliere
        PermessoUtente.objects.create(user=self.titolare, sezione='conteggio_cassa', consenti=False)
        self._post(self.titolare, azione='conteggio', contanti_totali='99')
        self.assertEqual(QuadraturaGiornaliera.objects.get().contanti_totali, Decimal('80.00'))
        self.assertNotContains(self.client.get(self.url), 'name="contanti_totali"')

    def test_scontrini_casse_dalla_distinta(self):
        from apps.auth_system.models import PermessoUtente
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        portale = Cassa.objects.create(nome='Portale Blu', tipo='automatica', tracking_washcycles=True)
        registratore = Cassa.objects.create(nome='Registratore', tipo='automatica', modalita_registratore=True)
        self.client.force_login(self.operatore)
        self.assertContains(self.client.get(self.url), 'Scontrini delle casse automatiche')
        self._post(self.operatore, azione='scontrino', cassa=portale.pk, incasso_totale='310,50',
                   incasso_ricarica='', vendita_contante='280', vendita_non_contante='20', wash_cycles='41')
        self._post(self.operatore, azione='scontrino', cassa=registratore.pk, incasso_totale='412.5',
                   vendita_contante='99')                                     # il registratore ha solo il totale
        c = ChiusuraCassaAutomatica.objects.get(cassa=portale)
        self.assertEqual((c.incasso_totale, c.incasso_ricarica, c.vendita_totale, c.wash_cycles, c.operatore),
                         (Decimal('310.50'), Decimal('0.00'), Decimal('300.00'), 41, self.operatore))
        r = ChiusuraCassaAutomatica.objects.get(cassa=registratore)
        self.assertEqual((r.incasso_totale, r.vendita_contante), (Decimal('412.50'), Decimal('0.00')))
        # aggiornare non tocca le note; importi negativi rifiutati
        ChiusuraCassaAutomatica.objects.filter(pk=c.pk).update(note='rotolo cambiato')
        self._post(self.operatore, azione='scontrino', cassa=portale.pk, incasso_totale='-1')
        self._post(self.operatore, azione='scontrino', cassa=portale.pk, incasso_totale='320')
        c.refresh_from_db()
        self.assertEqual((c.incasso_totale, c.note), (Decimal('320.00'), 'rotolo cambiato'))
        # confermato: non si modifica
        ChiusuraCassaAutomatica.objects.filter(pk=c.pk).update(confermata=True)
        self._post(self.operatore, azione='scontrino', cassa=portale.pk, incasso_totale='1')
        c.refresh_from_db()
        self.assertEqual(c.incasso_totale, Decimal('320.00'))
        # senza il permesso niente riquadro e niente salvataggio
        PermessoUtente.objects.create(user=self.operatore, sezione='scontrini_casse', consenti=False)
        self.operatore = User.objects.get(pk=self.operatore.pk)
        self.client.force_login(self.operatore)
        self.assertNotContains(self.client.get(self.url), 'Scontrini delle casse automatiche')
        self._post(self.operatore, azione='scontrino', cassa=registratore.pk, incasso_totale='1')
        r.refresh_from_db()
        self.assertEqual(r.incasso_totale, Decimal('412.50'))

    def test_pagina_e_permessi(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.auth_system.sezioni import ha_accesso, sezione_per_percorso
        from apps.finanze.views_chiusura import chiusura_giornata
        self.assertEqual(sezione_per_percorso(self.url), 'ordini')
        self.assertTrue(ha_accesso(self.operatore, 'ordini'))
        req = RequestFactory().get(self.url)
        req.user = User.objects.get(pk=self.operatore.pk)
        req.session = {}
        req._messages = FallbackStorage(req)
        pagina = chiusura_giornata(req).content.decode()
        for testo in ('Ricariche delle casse', 'Ticket di resto', 'Promo e omaggi', 'Spese pagate', 'Lavaggi extra',
                      'Il conteggio della cassa lo fa il titolare'):
            self.assertIn(testo, pagina)
        self.assertNotIn('name="contanti_totali"', pagina)    # il conteggio lo fa il titolare
        self.assertNotIn('Report giornata</a>', pagina)       # l'operatore non vede il report
        req.user = self.titolare
        pagina = chiusura_giornata(req).content.decode()
        self.assertIn('name="contanti_totali"', pagina)


class TransazioniWashtecTest(TestCase):
    """Pagina con l'archivio delle transazioni estratte da WashTec."""

    def setUp(self):
        from apps.finanze.models import RettificaResiduo
        self.admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        oggi = timezone.localdate()

        def alle(h, m=0):
            return timezone.make_aware(datetime.combine(oggi, datetime.min.time()).replace(hour=h, minute=m))
        self.contanti = TransazionePortale.objects.create(portale='A', numero=100, orario=alle(9), programma=1,
                                                          origine='contanti')
        self.unita = TransazionePortale.objects.create(portale='B', numero=200, orario=alle(10), programma=4,
                                                       origine='unita')
        self.omaggio = TransazionePortale.objects.create(portale='B', numero=201, orario=alle(11), programma=3,
                                                         origine='unita')
        RettificaResiduo.objects.create(transazione=self.omaggio, motivo='omaggio', importo=Decimal('0'))
        TransazionePortale.objects.create(portale='A', numero=103, orario=alle(12), programma=4, origine='unita')

    def _get(self, **params):
        from django.test import RequestFactory

        from apps.finanze.views_washtec import transazioni_washtec
        req = RequestFactory().get('/', params)
        req.user = self.admin
        return transazioni_washtec(req)

    def test_elenco_filtri_e_buchi(self):
        html = self._get().content.decode()
        self.assertIn('Transazioni WashTec', html)
        self.assertIn('Omaggio', html)
        self.assertIn('n. 101', html)                          # A: 100 -> 103, mancano 101-102
        self.assertIn('200', self._get(portale='B', origine='unita').content.decode())
        solo = self._get(stato='rettificata').content.decode()
        self.assertIn('201', solo)
        self.assertNotIn('>200<', solo)

    def test_csv(self):
        r = self._get(formato='csv')
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        testo = r.content.decode('utf-8-sig')
        self.assertEqual(len(testo.strip().splitlines()), 5)   # intestazione + 4
        self.assertIn('Omaggio', testo)


    def test_sezione_nel_report_giornata(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.finanze.views import report_giornata
        req = RequestFactory().get('/', {'data': timezone.localdate().isoformat()})
        req.user = self.admin
        req.session = {}
        req._messages = FallbackStorage(req)
        html = report_giornata(req).content.decode()
        self.assertIn('id="transazioni-washtec"', html)
        self.assertIn('Omaggio', html)
        self.assertIn('residuo &middot; pagato agli operatori', html)


class ImportAutomaticoWashtecTest(TestCase):
    """Import automatico (worker) e tasto "Aggiorna ora"."""

    def setUp(self):
        TransazionePortale.objects.create(portale='A', numero=13300, orario=ora('08:00'),
                                          programma=4, origine='unita')

    @staticmethod
    def estrai(dal, al):
        return [['13301', '06/10/26 10:00:00', '8', 'In contanti', '0,00 EUR', 'No', 'Sì'],
                ['13302', '06/10/26 10:20:00', '4', 'In contanti', '0,00 EUR', 'No', 'Sì']]

    def test_giro_salva_le_transazioni(self):
        from apps.finanze.services import import_automatico as ia
        e = ia.esegui(estrai=self.estrai)
        self.assertEqual((e.esito, e.righe, e.nuove), ('ok', 2, 2))
        self.assertEqual(TransazionePortale.objects.count(), 3)
        # idempotente: il giro dopo non trova nulla di nuovo
        self.assertEqual(ia.esegui(estrai=self.estrai).nuove, 0)
        self.assertEqual(ia.stato()['ultima_ok'].pk, ia.EsecuzioneImportWashtec.objects.first().pk)

    def test_errore_registrato_e_in_rosso(self):
        from apps.finanze.services import import_automatico as ia

        def rotto(dal, al):
            raise RuntimeError('sezione Report non trovata')
        e = ia.esegui(estrai=rotto)
        self.assertEqual(e.esito, 'errore')
        self.assertIn('Report', e.messaggio)
        stato = ia.stato()
        self.assertTrue(stato['allarme'])
        self.assertEqual(stato['errore'].pk, e.pk)

    def test_periodo(self):
        from apps.finanze.services import import_automatico as ia
        oggi = datetime(2026, 10, 9).date()
        # ultima transazione il 06/10: si riparte dal 05/10
        self.assertEqual(ia.periodo(oggi), (datetime(2026, 10, 5).date(), oggi))
        # archivio aggiornato: almeno da ieri
        self.assertEqual(ia.periodo(datetime(2026, 10, 6).date())[0], datetime(2026, 10, 5).date())
        TransazionePortale.objects.all().delete()
        self.assertEqual(ia.periodo(oggi)[0], datetime(2026, 10, 6).date())

    def test_aggiorna_ora_una_richiesta_e_il_worker_la_chiude(self):
        from apps.finanze.services import import_automatico as ia
        self.client.force_login(utente_titolare('tit', password='x'))
        url = reverse('ordini:washtec-aggiorna')
        self.client.post(url, HTTP_X_REQUESTED_WITH='fetch')
        r = self.client.post(url, HTTP_X_REQUESTED_WITH='fetch')
        self.assertTrue(r.json()['in_attesa'])
        self.assertEqual(ia.EsecuzioneImportWashtec.objects.filter(esito='richiesta').count(), 1)

        ia.ciclo(attesa=0, estrai=self.estrai, giri=1, scrivi=lambda *a: None)
        e = ia.EsecuzioneImportWashtec.objects.get()
        self.assertEqual((e.esito, e.nuove), ('ok', 2))
        self.assertIsNotNone(e.richiesta_da)
        self.assertFalse(self.client.get(reverse('ordini:washtec-stato')).json()['in_attesa'])

    def test_ciclo_sopravvive_a_un_errore(self):
        from unittest import mock

        from apps.finanze.services import import_automatico as ia
        ia.richiedi()
        log = []
        with mock.patch.object(ia, '_chiudi_giri_morti', side_effect=[Exception('db giu'), None, None]), \
                mock.patch.object(ia.orologio, 'sleep') as dorme:
            ia.ciclo(attesa=0, estrai=self.estrai, giri=2, scrivi=log.append, attesa_errore=60)
        self.assertIn('db giu', log[0])
        dorme.assert_called_once_with(60)
        self.assertEqual(ia.EsecuzioneImportWashtec.objects.get().esito, 'ok')

    def test_comando_senza_database_url(self):
        from django.core.management import CommandError, call_command
        with self.settings(DEBUG=False):
            with self.assertRaisesMessage(CommandError, 'DATABASE_URL'):
                call_command('importa_washtec_auto', '--ciclo')

    def test_richiesta_mai_presa_diventa_errore(self):
        from apps.finanze.services import import_automatico as ia
        e = ia.richiedi()
        ia.EsecuzioneImportWashtec.objects.filter(pk=e.pk).update(
            creata_il=timezone.now() - timedelta(minutes=5))
        stato = ia.stato()
        self.assertIsNone(stato['in_attesa'])
        self.assertIn('non risponde', stato['errore'].messaggio)


class SpazzoleOperatoriTest(TestCase):
    """Pagina Spazzole della Distinta: abbinamenti fatti dagli operatori."""

    def setUp(self):
        from django.contrib.auth.models import Group
        cat = Categoria.objects.create(nome='Servito')
        servizio = ServizioProdotto.objects.create(
            titolo='Lavaggio Completo', categoria=cat, prezzo=Decimal('22'), descrizione='',
            programmi_portale='8,4')
        from datetime import time
        self.giorno = timezone.localdate()
        adesso = timezone.make_aware(datetime.combine(self.giorno, time(12, 0)))
        ordine = Ordine.objects.create(totale=Decimal('22'), totale_finale=Decimal('22'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=adesso - timedelta(minutes=1))
        self.item = ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=servizio, quantita=1,
                                              prezzo_unitario=Decimal('22'))
        self.altro = ItemOrdine.objects.create(
            ordine=Ordine.objects.create(totale=Decimal('22'), totale_finale=Decimal('22')),
            servizio_prodotto=servizio, quantita=1, prezzo_unitario=Decimal('22'))
        Ordine.objects.filter(pk=self.altro.ordine_id).update(data_ora=adesso - timedelta(minutes=30))
        self.tx = TransazionePortale.objects.create(portale='A', numero=500, orario=adesso,
                                                    programma=8, origine='unita')
        self.operatore = User.objects.create_user('op', password='x')
        self.operatore.groups.add(Group.objects.get(name='operatore'))
        self.url = reverse('ordini:spazzole-giornata')

    def _post(self, **dati):
        self.client.force_login(self.operatore)
        return self.client.post(self.url, {'data': self.giorno.isoformat(), **dati})

    def test_operatore_conferma_e_chiude_senza_portale(self):
        self.client.force_login(self.operatore)
        r = self.client.get(self.url)
        self.assertContains(r, 'Conferma tutte le proposte (1)')
        self.assertContains(r, 'Aggiorna ora')
        self._post(azione='conferma')
        self.assertEqual(AbbinamentoPortale.objects.get().transazione_id, self.tx.pk)
        self._post(azione='senza_portale', item_id=self.altro.pk)
        from apps.finanze.views_spazzole import riepilogo_spazzole
        r = riepilogo_spazzole(self.giorno)
        self.assertEqual((r['n_ok'], r['n_proposto'], r['n_da_sistemare']), (2, 0, 0))
        # riquadro nella Distinta
        r = self.client.get(reverse('ordini:chiusura-giornata'))
        self.assertContains(r, 'tutto a posto')

    def test_senza_permesso(self):
        from apps.auth_system.models import PermessoUtente
        PermessoUtente.objects.create(user=self.operatore, sezione='spazzole_servito', consenti=False)
        self.client.force_login(self.operatore)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(reverse('ordini:washtec-aggiorna')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('ordini:chiusura-giornata')), 'Abbina spazzole')

    def test_giornata_confermata_non_si_modifica(self):
        from apps.finanze.models import QuadraturaGiornaliera
        QuadraturaGiornaliera.objects.create(data=self.giorno, confermata_il=timezone.now())
        self._post(azione='conferma')
        self.assertFalse(AbbinamentoPortale.objects.exists())


class ReportPeriodoQuadratureTest(TestCase):
    """Il report periodo somma le quadrature con la formula del report giornata."""

    def test_rilevato_con_spese_e_ticket(self):
        from datetime import date

        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.finanze.models import QuadraturaGiornaliera, SpesaCassa, TicketResto
        from apps.finanze.views import _saldo_giornata, report_periodo
        giorno = date(2026, 10, 6)
        QuadraturaGiornaliera.objects.create(data=giorno, contanti_totali=Decimal('100'))
        SpesaCassa.objects.create(data=giorno, descrizione='Gasolio', importo=Decimal('10'), riferimento='1')
        TicketResto.objects.create(data=giorno, importo=Decimal('5'))
        q, _ = _saldo_giornata(giorno)
        self.assertEqual((q['totale_reale'], q['differenza']), (Decimal('115'), Decimal('115')))
        req = RequestFactory().get('/', {'data_inizio': '2026-10-06', 'data_fine': '2026-10-06'})
        req.user = User.objects.create_superuser('boss2', 'b2@x.it', 'x')
        req.session = {}
        req._messages = FallbackStorage(req)
        html = report_periodo(req).content.decode()
        self.assertIn('Atteso in cassa', html)
        self.assertIn('&euro;115,00', html)          # rilevato con spese e ticket
        self.assertIn('+&euro;115,00', html)         # differenza come nel report giornata
