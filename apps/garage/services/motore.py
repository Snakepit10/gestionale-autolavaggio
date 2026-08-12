"""Motore del libretto: UNICO punto d'ingresso per registrare un
servizio su un veicolo, da qualsiasi canale (self service, operatore,
import futuri).

Fase attuale (F4): crea l'evento e aggiorna il punteggio salute
(prima applica il decadimento maturato dall'ultimo aggiornamento, poi
somma i punti del servizio, con cap 100 e floor 0).
La gamification completa (livelli, premi gettoni, badge, streak,
notifiche) si aggancia in _applica_gamification: F5 la riempie senza
toccare i chiamanti.
"""
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.garage.models import (EventoLibretto, ImpostazioniGarage,
                                TipoServizioGarage, Veicolo)


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


def _applica_gamification(veicolo, evento, esito, cfg):
    """Hook F5: livelli, premi gettoni, badge, streak, notifiche.

    Volutamente vuoto in F4: i chiamanti (self service, staff) non
    cambieranno quando verra' riempito.
    """
    return None
