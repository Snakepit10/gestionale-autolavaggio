"""Motore del libretto: UNICO punto d'ingresso per registrare un
servizio su un veicolo, da qualsiasi canale (self service, operatore,
import futuri).

Cosa fa registra_evento, in transazione con lock sul veicolo:
1. crea l'EventoLibretto;
2. salute: applica il decadimento maturato, somma i punti (cap/floor);
3. streak: la serie vive con almeno un servizio ogni
   streak_finestra_giorni; ai traguardi configurati premia in gettoni
   (una volta per serie: la chiave contiene l'inizio della serie);
4. badge milestone del servizio (idempotenti via unique DB);
5. livelli: se il percorso raggiunge un livello completato non ancora
   premiato -> PremioVeicolo + accredito wallet con causale 'premio' e
   chiave di idempotenza (doppia difesa: PremioVeicolo.chiave unique +
   chiave_idempotenza del wallet) + badge di livello;
6. eventi di dominio NotificaGarage (l'invio e' compito del notifier).
"""
import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.garage.models import (BadgeVeicolo, EventoLibretto,
                                ImpostazioniGarage, NotificaGarage,
                                PremioVeicolo, TipoServizioGarage, Veicolo)

logger = logging.getLogger(__name__)


@dataclass
class EsitoEvento:
    evento: EventoLibretto
    salute_prima: int
    salute_dopo: int
    # Riempiti dalla gamification (F5)
    livelli_completati: list = field(default_factory=list)
    badge_nuovi: list = field(default_factory=list)
    gettoni_premio: int = 0
    streak: int = 0


def _decadi(punteggio: int, dal, al, cfg) -> int:
    """Applica il decadimento maturato tra due istanti (giorni locali)."""
    if not cfg.decadimento_giorni:
        return punteggio
    giorni = (timezone.localtime(al).date()
              - timezone.localtime(dal).date()).days
    if giorni <= 0:
        return punteggio
    return max(0, punteggio
               - (giorni // cfg.decadimento_giorni) * cfg.decadimento_punti)


def registra_evento(veicolo: Veicolo, tipo_servizio: TipoServizioGarage,
                    origine: str, *, data=None, note: str = '',
                    registrato_da=None, movimento=None) -> EsitoEvento:
    """Registra un servizio sul libretto e aggiorna lo stato del veicolo.

    Transazione atomica con lock sul veicolo: due registrazioni
    simultanee (cliente + operatore) non si pestano i piedi.
    """
    adesso = data or timezone.now()
    cfg = ImpostazioniGarage.get_solo()

    with transaction.atomic():
        v = Veicolo.objects.select_for_update().get(pk=veicolo.pk)

        evento = EventoLibretto.objects.create(
            veicolo=v, tipo_servizio=tipo_servizio, origine=origine,
            data=adesso, note=note[:300], registrato_da=registrato_da,
            movimento=movimento,
        )

        # Salute: decadimento maturato + punti del servizio.
        salute_prima = _decadi(v.punteggio_salute, v.salute_aggiornata_il,
                               adesso, cfg)
        salute_dopo = max(0, min(100, salute_prima + tipo_servizio.punti_salute))
        v.punteggio_salute = salute_dopo
        # Il riferimento del decadimento avanza solo in avanti: una
        # registrazione retrodatata non deve "ringiovanire" il veicolo.
        if adesso > v.salute_aggiornata_il:
            v.salute_aggiornata_il = adesso
        campi = ['punteggio_salute', 'salute_aggiornata_il']

        if origine == 'self_service':
            v.ultimo_uso_self = adesso
            campi.append('ultimo_uso_self')
        v.save(update_fields=campi)

        esito = EsitoEvento(evento=evento, salute_prima=salute_prima,
                            salute_dopo=salute_dopo)
        _applica_gamification(v, evento, esito, cfg)

    # aggiorna l'istanza passata dal chiamante (senza altra query)
    veicolo.punteggio_salute = v.punteggio_salute
    veicolo.salute_aggiornata_il = v.salute_aggiornata_il
    veicolo.ultimo_uso_self = v.ultimo_uso_self
    return esito


def _premia(veicolo, chiave: str, gettoni: int, descrizione: str):
    """Accredita gettoni UNA sola volta per (veicolo, chiave).

    Doppia difesa: PremioVeicolo.chiave unique (idempotenza
    applicativa) + chiave_idempotenza sul wallet monete. Ritorna i
    gettoni accreditati (0 se gia' premiato o premio nullo).
    """
    from apps.monete.services import wallet
    from apps.monete.services.wallet import OperazioneDuplicataError

    try:
        # savepoint: su PostgreSQL un IntegrityError senza atomic
        # interno manderebbe in errore l'INTERA transazione esterna
        with transaction.atomic():
            premio = PremioVeicolo.objects.create(
                veicolo=veicolo, chiave=chiave, gettoni=gettoni,
                descrizione=descrizione[:200])
    except IntegrityError:
        return 0  # gia' premiato (replay/doppio submit)

    if gettoni < 1:
        return 0
    try:
        movimento = wallet.accredita(
            veicolo.cliente, gettoni, 'premio', descrizione[:200],
            chiave_idempotenza=f'garage:{veicolo.pk}:{chiave}'[:64])
        premio.movimento = movimento
        premio.save(update_fields=['movimento'])
    except OperazioneDuplicataError:
        return 0
    return gettoni


def _sblocca_badge(veicolo, slug: str, nome: str, icona: str, esito):
    badge, creato = BadgeVeicolo.objects.get_or_create(
        veicolo=veicolo, slug=slug[:60],
        defaults={'nome': nome[:100], 'icona': icona or 'bi-award'})
    if creato:
        esito.badge_nuovi.append(badge.nome)
        _notifica(veicolo, 'badge_sbloccato', {'badge': badge.nome},
                  chiave_dedup=f'badge:{slug}'[:100])
    return creato


def _notifica(veicolo, tipo: str, payload: dict, chiave_dedup: str = ''):
    """Evento di dominio: il notifier (F7) decide se e come inviarlo."""
    try:
        with transaction.atomic():   # savepoint (vedi _premia)
            NotificaGarage.objects.create(
                veicolo=veicolo, tipo=tipo, payload=payload,
                chiave_dedup=chiave_dedup)
    except IntegrityError:
        pass  # dedup: gia' notificato


def _aggiorna_streak(veicolo, evento, esito, cfg):
    """Serie di servizi consecutivi entro la finestra configurata.

    Un evento retrodatato (data <= ultimo evento streak) allunga il
    conteggio senza toccare il riferimento; il reset scatta solo
    quando il NUOVO evento arriva oltre la finestra dall'ultimo.
    """
    data = evento.data
    ultimo = veicolo.streak_ultimo_evento
    if ultimo is None or veicolo.streak_conteggio == 0:
        veicolo.streak_conteggio = 1
        veicolo.streak_iniziata_il = data
    elif data > ultimo and (timezone.localtime(data).date()
                            - timezone.localtime(ultimo).date()).days \
            > cfg.streak_finestra_giorni:
        # finestra scaduta: la serie riparte da questo evento
        veicolo.streak_conteggio = 1
        veicolo.streak_iniziata_il = data
    else:
        veicolo.streak_conteggio += 1
    if ultimo is None or data > ultimo:
        veicolo.streak_ultimo_evento = data
    veicolo.save(update_fields=['streak_conteggio', 'streak_iniziata_il',
                                'streak_ultimo_evento'])
    esito.streak = veicolo.streak_conteggio

    # Traguardi: premio per OGNI serie che raggiunge la soglia (la
    # chiave contiene l'inizio serie: una nuova serie ripremia).
    traguardi = cfg.streak_traguardi or {}
    gettoni = traguardi.get(str(veicolo.streak_conteggio))
    if gettoni:
        # microsecondi inclusi: due serie iniziate nello stesso secondo
        # (improbabile ma possibile) non devono condividere la chiave
        inizio = timezone.localtime(veicolo.streak_iniziata_il)
        vinti = _premia(
            veicolo,
            f'streak:{inizio:%Y%m%d%H%M%S%f}:{veicolo.streak_conteggio}',
            int(gettoni),
            f'Premio streak {veicolo.streak_conteggio} servizi - {veicolo.targa}')
        esito.gettoni_premio += vinti


def _controlla_livelli(veicolo, esito, cfg):
    """Premia i livelli completati non ancora premiati (idempotente)."""
    from .percorso import stato_percorso

    stato = stato_percorso(veicolo)
    for ls in stato.livelli:
        if ls.stato != 'completato':
            continue
        livello = ls.livello
        if PremioVeicolo.objects.filter(
                veicolo=veicolo, chiave=f'livello:{livello.pk}').exists():
            continue
        vinti = _premia(
            veicolo, f'livello:{livello.pk}', livello.premio_gettoni,
            f'Premio Livello {livello.numero} ({livello.nome}) - {veicolo.targa}')
        esito.gettoni_premio += vinti
        esito.livelli_completati.append(livello)
        if livello.badge_nome:
            _sblocca_badge(veicolo, f'livello-{livello.numero}',
                           livello.badge_nome, livello.badge_icona, esito)
        _notifica(veicolo, 'livello_completato',
                  {'livello': livello.numero, 'nome': livello.nome,
                   'gettoni': livello.premio_gettoni},
                  chiave_dedup=f'livello:{livello.pk}')
    if stato.nel_club:
        _sblocca_badge(veicolo, 'club-auto-perfetta', 'Club Auto Perfetta',
                       'bi-trophy-fill', esito)


def _applica_gamification(veicolo, evento, esito, cfg):
    """Streak, badge milestone, livelli con premi in gettoni, eventi
    di dominio. Gira DENTRO la transazione di registra_evento."""
    _aggiorna_streak(veicolo, evento, esito, cfg)

    tipo = evento.tipo_servizio
    if tipo.badge_nome:
        _sblocca_badge(veicolo, f'servizio-{tipo.slug}', tipo.badge_nome,
                       tipo.badge_icona, esito)

    _controlla_livelli(veicolo, esito, cfg)
