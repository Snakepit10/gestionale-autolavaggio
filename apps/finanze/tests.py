"""Test abbinamento lavaggi servito <-> transazioni portale unita'."""
from datetime import datetime, timedelta
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
        self._tx('10:40', 4)                    # successiva al completamento
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
        self._tx('14:00', 4)                    # oltre 2h prima dell'obiettivo
        TransazionePortale.objects.create(      # fuori finestra
            portale='B', numero=13999, orario=ora('18:30'),
            programma=4, origine='unita')
        self.assertEqual(ap.proponi(self.chiusura), [])

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
        user = User.objects.create_user('op', 'op@x.it', 'x', is_staff=True)
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
