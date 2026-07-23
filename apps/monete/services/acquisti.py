"""Accredito degli acquisti online (comune a Stripe e PayPal).

L'accredito e' idempotente DUE volte:
1. transizione di stato con select_for_update: due webhook (o webhook +
   pagina di esito) concorrenti si serializzano e il secondo trova gia'
   stato='accreditato' -> no-op;
2. chiave di idempotenza 'acquisto:<pk>' sul movimento: anche in caso
   di replay patologico il ledger rifiuta il doppione.
"""
import logging

from django.db import transaction

from apps.monete.models import AcquistoMonete
from . import wallet

logger = logging.getLogger('apps.monete.acquisti')


def accredita_acquisto(acquisto_id: int) -> tuple:
    """Accredita le monete di un acquisto pagato. Ritorna (ok, messaggio).

    Sicuro da chiamare piu' volte (webhook rieseguiti, refresh della
    pagina di esito): accredita una sola volta.
    """
    with transaction.atomic():
        try:
            acquisto = AcquistoMonete.objects.select_for_update().get(
                pk=acquisto_id)
        except AcquistoMonete.DoesNotExist:
            return False, f'Acquisto {acquisto_id} inesistente.'

        if acquisto.stato == 'accreditato':
            return True, 'Acquisto gia\' accreditato.'
        if acquisto.stato == 'annullato':
            return False, 'Acquisto annullato: nessun accredito.'

        acquisto.stato = 'accreditato'
        acquisto.save(update_fields=['stato', 'aggiornato_il'])

        try:
            wallet.accredita(
                acquisto.cliente, acquisto.monete, 'acquisto_online',
                f'Acquisto {acquisto.monete} monete via '
                f'{acquisto.get_provider_display()}',
                acquisto=acquisto, importo=acquisto.importo,
                chiave_idempotenza=f'acquisto:{acquisto.pk}',
            )
        except wallet.OperazioneDuplicataError:
            # Il movimento esiste gia' (replay estremo): stato allineato,
            # saldo gia' corretto.
            logger.warning('Accredito acquisto %s: movimento gia\' presente.',
                           acquisto.pk)

    logger.info('Acquisto %s accreditato: +%s monete a cliente %s.',
                acquisto.pk, acquisto.monete, acquisto.cliente_id)
    return True, f'{acquisto.monete} monete accreditate.'
