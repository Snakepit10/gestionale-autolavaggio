"""Riconciliazione tra credito scalato e impulsi contati dalla macchina.

Il COUNT IN dello Shelly conta gli impulsi che la gettoniera ha
davvero ricevuto (fisici + virtuali) e arriva in EventoImpianto via
listener MQTT. Flusso:

1. avvia_lavaggio fotografa il totale del contatore PRIMA dell'invio
   (contatore_prima) e, a invio concluso, calcola l'atteso
   (contatore_atteso = prima + impulsi inviati) -> verifica='in_attesa';
2. il listener, a ogni nuovo totale del contatore, chiama
   aggiorna_verifiche(): se il totale ha raggiunto l'atteso ->
   verifica='ok' con l'avanzamento osservato;
3. il cron (monete_riconcilia) chiama scadenza_verifiche(): i lavaggi
   non confermati entro la finestra diventano 'discrepanza' -> da
   controllare in admin (eventuale rimborso manuale).

Nota: se una moneta fisica entra nello stesso momento il contatore
avanza di piu' dell'atteso -> resta 'ok' (mai falsi allarmi in
eccesso); il caso patologico coperto e' il contatore che avanza MENO
degli impulsi pagati.
"""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger('apps.monete.verifica')

# Entro quanti minuti il contatore deve confermare gli impulsi
FINESTRA_CONFERMA_MIN = 5


def _ultimo_totale(nodo_slug: str):
    from apps.impianto.models import EventoImpianto
    return (EventoImpianto.objects
            .filter(nodo=nodo_slug, tipo_evento='contatore')
            .order_by('-pk')
            .values_list('valore', flat=True)
            .first())


def baseline_contatore(nodo) -> int | None:
    """Totale del contatore prima dell'invio (None se mai visto)."""
    return _ultimo_totale(nodo.slug)


def registra_esito_invio(movimento, baseline, inviati: int):
    """Completa il movimento 'lavaggio' con i dati per la verifica."""
    if baseline is None or inviati <= 0:
        movimento.verifica = 'non_verificabile'
        movimento.save(update_fields=['verifica'])
        return
    movimento.contatore_prima = baseline
    movimento.contatore_atteso = baseline + inviati
    movimento.verifica = 'in_attesa'
    movimento.save(update_fields=['contatore_prima', 'contatore_atteso',
                                  'verifica'])


def aggiorna_verifiche(nodo_slug: str, totale: int):
    """Chiamata dal listener MQTT a ogni nuovo totale del contatore:
    conferma i lavaggi in attesa che l'hanno raggiunto."""
    from apps.monete.models import MovimentoMoneta

    pendenti = MovimentoMoneta.objects.filter(
        tipo='lavaggio', verifica='in_attesa', nodo__slug=nodo_slug)
    for mv in pendenti:
        if mv.contatore_atteso is not None and totale >= mv.contatore_atteso:
            mv.impulsi_contati = totale - (mv.contatore_prima or 0)
            mv.verifica = 'ok'
            mv.save(update_fields=['impulsi_contati', 'verifica'])
            logger.info('Lavaggio %s confermato dal contatore (%s >= %s).',
                        mv.pk, totale, mv.contatore_atteso)


def scadenza_verifiche(finestra_min: int = FINESTRA_CONFERMA_MIN) -> int:
    """Per il cron: marca 'discrepanza' i lavaggi non confermati entro
    la finestra. Ritorna quante discrepanze trovate."""
    from apps.monete.models import MovimentoMoneta

    soglia = timezone.now() - timedelta(minutes=finestra_min)
    discrepanze = 0
    pendenti = MovimentoMoneta.objects.filter(
        tipo='lavaggio', verifica='in_attesa', creato_il__lt=soglia,
    ).select_related('nodo', 'cliente')

    for mv in pendenti:
        ultimo = _ultimo_totale(mv.nodo.slug) if mv.nodo else None
        contati = None
        if ultimo is not None and mv.contatore_prima is not None:
            contati = max(0, ultimo - mv.contatore_prima)
        mv.impulsi_contati = contati
        if (contati is not None and mv.contatore_atteso is not None
                and ultimo >= mv.contatore_atteso):
            mv.verifica = 'ok'
        else:
            mv.verifica = 'discrepanza'
            discrepanze += 1
            logger.warning(
                'DISCREPANZA lavaggio %s: cliente=%s nodo=%s impulsi_pagati=%s '
                'contati=%s (atteso totale %s, ultimo %s)',
                mv.pk, mv.cliente_id, mv.nodo.slug if mv.nodo else '?',
                mv.impulsi, contati, mv.contatore_atteso, ultimo)
        mv.save(update_fields=['impulsi_contati', 'verifica'])
    return discrepanze
