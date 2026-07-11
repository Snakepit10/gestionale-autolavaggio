"""Processamento della coda invii campagne.

Unico punto d'ingresso `processa_coda()`, usato da:
- management command esegui_campagne_marketing (cron Railway)
- bottone "Processa coda ora" nella UI (thread daemon dal web process)

Concurrency-safety su due livelli:
- lock in-process (threading.Lock non bloccante): se il bottone viene
  premuto due volte, il secondo run esce subito;
- claim atomico per-riga tra processi diversi (cron vs web): prima di
  inviare, UPDATE ... WHERE stato='in_coda' AND inviato_il IS NULL
  imposta inviato_il come marcatore. Solo il processo che vince
  l'update (1 riga) procede con l'invio; l'altro salta la riga.
"""
import random
import threading
import time

from django.utils import timezone

from apps.marketing.models import Campagna, ImpostazioniMarketing, InvioCampagna
from .campagne import risolvi_params, verifica_eleggibilita

_LOCK = threading.Lock()


def processa_coda(max_batch: int = 8, log=None, dry: bool = False) -> dict:
    """Invia fino a max_batch messaggi dalla coda, rispettando il tetto
    giornaliero e le pause casuali. Ritorna contatori.
    """
    log = log or (lambda m: None)

    if not _LOCK.acquire(blocking=False):
        log('Un altro processamento coda e\' gia\' in corso in questo processo: skip.')
        return {'lock_occupato': True}

    try:
        return _processa(max_batch, log, dry)
    finally:
        _LOCK.release()


def _processa(max_batch, log, dry):
    cfg = ImpostazioniMarketing.get_solo()
    esiti = {'inviati': 0, 'falliti': 0, 'saltati': 0}

    # Budget giornaliero: tetto - inviati oggi (tutte le campagne)
    inizio_oggi = timezone.localtime(timezone.now()).replace(
        hour=0, minute=0, second=0, microsecond=0)
    inviati_oggi = InvioCampagna.objects.filter(
        stato='inviato', inviato_il__gte=inizio_oggi).count()
    budget = max(0, cfg.max_invii_giorno - inviati_oggi)
    if budget == 0:
        log(f'Tetto giornaliero raggiunto ({inviati_oggi}/{cfg.max_invii_giorno}): stop.')
        return esiti
    batch = min(budget, max_batch)

    # inviato_il null = non ancora "claimata" da nessun processo
    in_coda = list(
        InvioCampagna.objects
        .filter(stato='in_coda', inviato_il__isnull=True,
                campagna__stato__in=['in_coda', 'in_corso'])
        .select_related('campagna', 'cliente')
        .order_by('campagna__lanciata_il', 'pk')[:batch]
    )
    if not in_coda:
        log('Coda invii vuota.')
        return esiti

    log(f'Processo {len(in_coda)} invii (budget residuo {budget}, batch {max_batch}).')

    from apps.clients import whatsapp as wa
    from apps.messaggi.models import MessaggioWhatsApp

    for idx, invio in enumerate(in_coda):
        campagna = invio.campagna
        cliente = invio.cliente

        if dry:
            log(f'[dry-run] {cliente} <- template={campagna.template_meta}')
            continue

        # Claim atomico: solo un processo vince questa riga.
        claimed = InvioCampagna.objects.filter(
            pk=invio.pk, stato='in_coda', inviato_il__isnull=True,
        ).update(inviato_il=timezone.now())
        if not claimed:
            continue  # preso da un altro processo (cron/web)

        if campagna.stato == 'in_coda':
            campagna.stato = 'in_corso'
            campagna.save(update_fields=['stato'])

        # Re-check eleggibilita' al momento dell'invio
        esito = verifica_eleggibilita(cliente, cfg, escludi_campagna=campagna)
        if not esito.eleggibile:
            InvioCampagna.objects.filter(pk=invio.pk).update(
                stato='saltato', motivo_salto=esito.motivo, inviato_il=None)
            esiti['saltati'] += 1
            log(f'{cliente}: saltato ({esito.motivo})')
            continue

        to_e164 = wa._to_e164(cliente.telefono)
        if not to_e164:
            InvioCampagna.objects.filter(pk=invio.pk).update(
                stato='saltato', motivo_salto='telefono non valido', inviato_il=None)
            esiti['saltati'] += 1
            continue

        params = risolvi_params(campagna.template_params, cliente)
        ok, wa_id = wa._send_template_blocking(to_e164, campagna.template_meta, params)

        if ok:
            msg = MessaggioWhatsApp.objects.filter(wa_message_id=wa_id).first() if wa_id else None
            InvioCampagna.objects.filter(pk=invio.pk).update(
                stato='inviato', inviato_il=timezone.now(),
                messaggio_wa=msg)
            esiti['inviati'] += 1
            log(f'Inviato a {cliente}')
        else:
            InvioCampagna.objects.filter(pk=invio.pk).update(stato='fallito')
            esiti['falliti'] += 1
            log(f'FALLITO invio a {cliente}')

        # Pausa casuale tra invii (non dopo l'ultimo)
        if idx < len(in_coda) - 1:
            pausa = random.randint(cfg.intervallo_min_secondi, cfg.intervallo_max_secondi)
            log(f'  pausa {pausa}s...')
            time.sleep(pausa)

    # Chiudi le campagne manuali esaurite
    if not dry:
        for campagna in Campagna.objects.filter(stato='in_corso', tipo='manuale'):
            if not campagna.invii.filter(stato='in_coda').exists():
                campagna.stato = 'completata'
                campagna.completata_il = timezone.now()
                campagna.save(update_fields=['stato', 'completata_il'])
                log(f'Campagna "{campagna.nome}" completata.')

    return esiti


def avvia_processamento_background(max_batch: int = 8) -> None:
    """Lancia processa_coda in thread daemon (per il bottone UI).

    Il web process (daphne) resta vivo dopo la response, quindi il
    thread ha tempo di completare gli invii con le pause. Il logging va
    sul logger Python (visibile nei log Railway).
    """
    import logging
    logger = logging.getLogger('apps.marketing.invio')

    threading.Thread(
        target=processa_coda,
        kwargs={'max_batch': max_batch, 'log': logger.info},
        daemon=True,
    ).start()
