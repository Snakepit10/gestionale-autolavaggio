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
        r = ap.riepilogo(self.chiusura)
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
        r = ap.riepilogo(self.chiusura)
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

    def test_tolleranza_e_finestra(self):
        self._item(self.completo, completato='17:30')
        self._tx('14:00', 7)                    # oltre 2h prima dell'obiettivo
        TransazionePortale.objects.create(      # fuori finestra
            portale='B', numero=13999, orario=ora('18:30'),
            programma=4, origine='unita')
        self.assertEqual(ap.proponi(self.chiusura), [])

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
        self.assertEqual(ap.riepilogo(self.chiusura)['n_scoperti'], 1)

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
        self._tx('10:05', 4)                    # abbinata al servito
        self._tx('15:00', 1)                    # residuo 15 euro
        self._tx('16:00', 9)                    # residuo senza prezzo
        r = ap.riepilogo(self.chiusura)
        self.assertEqual(r['n_proposte'], 1)
        self.assertEqual(len(r['residuo']), 2)
        self.assertEqual(r['valore_residuo'], Decimal('15.00'))
        self.assertEqual(r['residuo_senza_prezzo'], 1)

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
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, p4.pk)
        self.assertFalse(ok)                    # programma incompatibile
        ok, _ = ap.abbina_manuale(self.chiusura, item.pk, p5.pk)
        self.assertTrue(ok)
        item2 = self._item(self.a_mano, completato='12:10')
        ok, _ = ap.abbina_manuale(self.chiusura, item2.pk, p5.pk)
        self.assertFalse(ok)                    # transazione gia' usata

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
