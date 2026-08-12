"""Test del motore gamification del Garage.

Coprono i requisiti della spec: transizioni di livello, calcolo e
decadimento punteggio, idempotenza premi, streak (attivazione,
traguardi, azzeramento), scadenze trattamenti.

Esecuzione: python manage.py test apps.garage
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.clienti.models import Cliente
from apps.monete.services import wallet

from .models import (BadgeVeicolo, ImpostazioniGarage, ItemLivello,
                     LivelloPercorso, Percorso, PremioVeicolo,
                     TipoServizioGarage, Veicolo)
from .services.motore import registra_evento
from .services.percorso import stato_percorso
from .services.scadenze import trattamenti_veicolo


class GarageBaseTest(TestCase):
    """Base: percorso mini (2 livelli) costruito ad hoc, indipendente
    dal seed (che comunque gira nelle migration del test DB)."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome='Test', cognome='Garage', tipo='privato',
            telefono='+393400000001')
        self.cfg = ImpostazioniGarage.get_solo()

        self.percorso = Percorso.objects.create(nome='Percorso test')
        self.t_esterno = TipoServizioGarage.objects.create(
            slug='t-esterno', nome='Esterno T', punti_salute=3)
        self.t_interno = TipoServizioGarage.objects.create(
            slug='t-interno', nome='Interno T', punti_salute=3,
            badge_nome='Interni Top', badge_icona='bi-stars')
        self.t_coating = TipoServizioGarage.objects.create(
            slug='t-coating', nome='Coating T', punti_salute=12,
            validita_giorni=450)
        self.t_self = TipoServizioGarage.objects.create(
            slug='self_service_t', nome='Self T', punti_salute=2)

        self.l1 = LivelloPercorso.objects.create(
            percorso=self.percorso, numero=1, nome='Base T',
            premio_gettoni=2, badge_nome='Base T fatta')
        ItemLivello.objects.create(livello=self.l1,
                                   tipo_servizio=self.t_esterno,
                                   soddisfatto_da_self=False)
        ItemLivello.objects.create(livello=self.l1,
                                   tipo_servizio=self.t_interno)
        self.l2 = LivelloPercorso.objects.create(
            percorso=self.percorso, numero=2, nome='Pro T',
            premio_gettoni=4)
        ItemLivello.objects.create(livello=self.l2,
                                   tipo_servizio=self.t_coating)

        self.v = Veicolo.objects.create(
            cliente=self.cliente, targa='TT111TT', marca='Fiat',
            modello='Uno', percorso=self.percorso)


class TransizioniLivelloTest(GarageBaseTest):
    def test_livello_si_sblocca_solo_completando_il_precedente(self):
        stato = stato_percorso(self.v)
        self.assertEqual(stato.corrente.livello.numero, 1)
        self.assertEqual(stato.livelli[1].stato, 'bloccato')

        registra_evento(self.v, self.t_esterno, 'operatore')
        stato = stato_percorso(self.v)
        self.assertEqual(stato.corrente.livello.numero, 1)
        self.assertEqual(stato.corrente.n_fatti, 1)

        registra_evento(self.v, self.t_interno, 'operatore')
        stato = stato_percorso(self.v)
        self.assertEqual(stato.livelli[0].stato, 'completato')
        self.assertEqual(stato.corrente.livello.numero, 2)
        self.assertEqual(stato.livelli[1].stato, 'attivo')

    def test_club_al_completamento_di_tutti_i_livelli(self):
        registra_evento(self.v, self.t_esterno, 'operatore')
        registra_evento(self.v, self.t_interno, 'operatore')
        registra_evento(self.v, self.t_coating, 'operatore')
        stato = stato_percorso(self.v)
        self.assertTrue(stato.nel_club)
        self.assertTrue(BadgeVeicolo.objects.filter(
            veicolo=self.v, slug='club-auto-perfetta').exists())

    def test_item_soddisfatto_da_self(self):
        item = ItemLivello.objects.get(livello=self.l1,
                                       tipo_servizio=self.t_esterno)
        item.soddisfatto_da_self = True
        item.save()
        # il tipo self del percorso di test non e' 'self_service' del
        # seed: il check di percorso.py usa lo slug fisso, quindi qui
        # registriamo un evento col tipo seed vero
        tipo_seed = TipoServizioGarage.objects.get(slug='self_service')
        registra_evento(self.v, tipo_seed, 'self_service')
        stato = stato_percorso(self.v)
        fatto = next(i for i in stato.livelli[0].items
                     if i.slug == 't-esterno')
        self.assertTrue(fatto.completato)


class SaluteTest(GarageBaseTest):
    def test_punti_sommati_con_cap_100(self):
        Veicolo.objects.filter(pk=self.v.pk).update(punteggio_salute=95)
        self.v.refresh_from_db()
        esito = registra_evento(self.v, self.t_coating, 'operatore')
        self.assertEqual(esito.salute_dopo, 100)

    def test_decadimento_applicato_prima_dei_punti(self):
        # 50 punti, fermo 30 giorni (2 cicli da 14gg) -> 48, +3 = 51
        Veicolo.objects.filter(pk=self.v.pk).update(
            salute_aggiornata_il=timezone.now() - timedelta(days=30))
        self.v.refresh_from_db()
        esito = registra_evento(self.v, self.t_esterno, 'operatore')
        self.assertEqual(esito.salute_prima, 48)
        self.assertEqual(esito.salute_dopo, 51)

    def test_decadimento_lazy_in_lettura_con_floor(self):
        Veicolo.objects.filter(pk=self.v.pk).update(
            punteggio_salute=3,
            salute_aggiornata_il=timezone.now() - timedelta(days=100))
        self.v.refresh_from_db()
        self.assertEqual(self.v.salute_attuale, 0)


class IdempotenzaPremiTest(GarageBaseTest):
    def test_premio_livello_una_sola_volta(self):
        registra_evento(self.v, self.t_esterno, 'operatore')
        registra_evento(self.v, self.t_interno, 'operatore')
        self.assertEqual(wallet.saldo_di(self.cliente), 2)

        # eventi ripetuti sugli stessi servizi: nessun nuovo premio
        registra_evento(self.v, self.t_interno, 'operatore')
        registra_evento(self.v, self.t_esterno, 'operatore')
        self.assertEqual(wallet.saldo_di(self.cliente), 2)
        self.assertEqual(PremioVeicolo.objects.filter(
            veicolo=self.v, chiave=f'livello:{self.l1.pk}').count(), 1)

    def test_badge_milestone_una_sola_volta(self):
        registra_evento(self.v, self.t_interno, 'operatore')
        registra_evento(self.v, self.t_interno, 'operatore')
        self.assertEqual(BadgeVeicolo.objects.filter(
            veicolo=self.v, slug='servizio-t-interno').count(), 1)

    def test_premio_per_veicolo_non_per_cliente(self):
        v2 = Veicolo.objects.create(
            cliente=self.cliente, targa='TT222TT', marca='Fiat',
            modello='Due', percorso=self.percorso)
        for veicolo in (self.v, v2):
            registra_evento(veicolo, self.t_esterno, 'operatore')
            registra_evento(veicolo, self.t_interno, 'operatore')
        # 2 gettoni per ciascun veicolo
        self.assertEqual(wallet.saldo_di(self.cliente), 4)


class StreakTest(GarageBaseTest):
    def test_attivazione_e_conteggio(self):
        registra_evento(self.v, self.t_esterno, 'operatore')
        self.v.refresh_from_db()
        self.assertEqual(self.v.streak_conteggio, 1)
        registra_evento(self.v, self.t_interno, 'operatore')
        self.v.refresh_from_db()
        self.assertEqual(self.v.streak_conteggio, 2)
        self.assertTrue(self.v.streak_attiva)

    def test_traguardo_5_premia_1_gettone(self):
        saldo_iniziale = wallet.saldo_di(self.cliente)
        for _ in range(5):
            registra_evento(self.v, self.t_self, 'self_service')
        self.v.refresh_from_db()
        self.assertEqual(self.v.streak_conteggio, 5)
        # +1 del traguardo streak (il percorso test non si completa col self)
        self.assertEqual(wallet.saldo_di(self.cliente), saldo_iniziale + 1)
        # replay dello stesso traguardo: nessun doppio premio
        chiavi = PremioVeicolo.objects.filter(
            veicolo=self.v, chiave__startswith='streak:').count()
        self.assertEqual(chiavi, 1)

    def test_azzeramento_oltre_finestra_e_nuovo_premio(self):
        # serie 1: arriva a 5 (premio 1)
        for _ in range(5):
            registra_evento(self.v, self.t_self, 'self_service')
        saldo_dopo_prima = wallet.saldo_di(self.cliente)

        # la serie scade: sposta l'ultimo evento a 30 giorni fa
        Veicolo.objects.filter(pk=self.v.pk).update(
            streak_ultimo_evento=timezone.now() - timedelta(days=30),
            streak_iniziata_il=timezone.now() - timedelta(days=60))
        self.v.refresh_from_db()
        self.assertFalse(self.v.streak_attiva)

        # il prossimo evento RIPARTE da 1
        registra_evento(self.v, self.t_self, 'self_service')
        self.v.refresh_from_db()
        self.assertEqual(self.v.streak_conteggio, 1)

        # nuova serie fino a 5: NUOVO premio (per ogni serie)
        for _ in range(4):
            registra_evento(self.v, self.t_self, 'self_service')
        self.assertEqual(wallet.saldo_di(self.cliente), saldo_dopo_prima + 1)


class ScadenzeTest(GarageBaseTest):
    def test_trattamento_attivo_e_scaduto(self):
        registra_evento(self.v, self.t_coating, 'operatore')
        tratt = trattamenti_veicolo(self.v)
        self.assertEqual(len(tratt), 1)
        self.assertEqual(tratt[0].stato, 'attivo')
        self.assertEqual(tratt[0].giorni_rimanenti, 450)

        # retrodata l'evento oltre la validita' -> scaduto
        self.v.eventi.update(data=timezone.now() - timedelta(days=460))
        tratt = trattamenti_veicolo(self.v)
        self.assertEqual(tratt[0].stato, 'scaduto')
        self.assertEqual(tratt[0].giorni_rimanenti, -10)

    def test_vale_l_ultimo_evento_del_tipo(self):
        e1 = registra_evento(self.v, self.t_coating, 'operatore',
                             data=timezone.now() - timedelta(days=400))
        registra_evento(self.v, self.t_coating, 'operatore')
        tratt = trattamenti_veicolo(self.v)
        self.assertEqual(len(tratt), 1)
        self.assertEqual(tratt[0].giorni_rimanenti, 450)

    def test_in_scadenza_sotto_30_giorni(self):
        registra_evento(self.v, self.t_coating, 'operatore',
                        data=timezone.now() - timedelta(days=430))
        tratt = trattamenti_veicolo(self.v)
        self.assertEqual(tratt[0].stato, 'in_scadenza')
