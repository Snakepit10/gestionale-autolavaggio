"""Integrazione Stripe Checkout per l'acquisto pacchetti monete.

Flusso:
1. il cliente sceglie un pacchetto -> creiamo AcquistoMonete (stato
   'creato') e una Checkout Session Stripe -> redirect alla pagina di
   pagamento ospitata da Stripe;
2. a pagamento riuscito Stripe chiama il webhook firmato
   (/monete/webhook/stripe/) con checkout.session.completed ->
   accredita_acquisto;
3. il cliente torna sulla pagina di esito, che fa anche da FALLBACK:
   se il webhook non e' ancora arrivato (o non e' configurato),
   verifica la session via API e accredita direttamente.

Env: STRIPE_SECRET_KEY (sk_test_.../sk_live_...),
STRIPE_WEBHOOK_SECRET (whsec_..., dal Dashboard o da `stripe listen`).
"""
import logging

from django.conf import settings
from django.urls import reverse

import stripe

from apps.monete.models import AcquistoMonete
from .acquisti import accredita_acquisto

logger = logging.getLogger('apps.monete.stripe')


def stripe_configurato() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def crea_sessione(acquisto: AcquistoMonete, request) -> str:
    """Crea la Checkout Session e ritorna l'URL di pagamento Stripe."""
    stripe.api_key = settings.STRIPE_SECRET_KEY

    success_url = (request.build_absolute_uri(
        reverse('monete_client:acquisto-esito'))
        + '?session_id={CHECKOUT_SESSION_ID}')
    cancel_url = (request.build_absolute_uri(
        reverse('monete_client:acquisto-annullato'))
        + f'?acquisto={acquisto.pk}')

    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{
            'price_data': {
                'currency': 'eur',
                'unit_amount': int(acquisto.importo * 100),  # centesimi
                'product_data': {
                    'name': f'{acquisto.monete} monete virtuali MasterWash',
                },
            },
            'quantity': 1,
        }],
        metadata={'acquisto_id': str(acquisto.pk)},
        client_reference_id=str(acquisto.cliente_id),
        success_url=success_url,
        cancel_url=cancel_url,
    )
    acquisto.provider_ref = session.id
    acquisto.save(update_fields=['provider_ref', 'aggiornato_il'])
    logger.info('Checkout Session %s creata per acquisto %s.',
                session.id, acquisto.pk)
    return session.url


def gestisci_evento_webhook(payload: bytes, firma: str) -> tuple:
    """Verifica la firma e processa l'evento. Ritorna (status_http, msg)."""
    try:
        evento = stripe.Webhook.construct_event(
            payload, firma, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning('Webhook Stripe rifiutato: %s', exc)
        return 400, 'firma non valida'

    if evento['type'] == 'checkout.session.completed':
        # Gli StripeObject delle versioni recenti non sono dict (.get
        # non esiste): accesso a indice con fallback esplicito.
        sess = evento['data']['object']
        metadata = _campo(sess, 'metadata') or {}
        acquisto_id = _campo(metadata, 'acquisto_id')
        if _campo(sess, 'payment_status') == 'paid' and acquisto_id:
            ok, msg = accredita_acquisto(int(acquisto_id))
            logger.info('Webhook checkout.session.completed acquisto %s: %s',
                        acquisto_id, msg)
    return 200, 'ok'


def _campo(obj, chiave, default=None):
    """Accesso sicuro a StripeObject/dict indifferentemente."""
    try:
        return obj[chiave]
    except (KeyError, TypeError, IndexError):
        return default


def verifica_e_accredita(session_id: str, cliente) -> tuple:
    """Fallback della pagina di esito: interroga la session e accredita.

    Ritorna (ok, messaggio, acquisto|None). Accetta solo session il cui
    acquisto appartiene al cliente loggato.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        logger.warning('Retrieve session %s fallita: %s', session_id, exc)
        return False, 'Sessione di pagamento non trovata.', None

    acquisto = AcquistoMonete.objects.filter(
        provider='stripe', provider_ref=session_id, cliente=cliente).first()
    if acquisto is None:
        return False, 'Acquisto non trovato.', None

    if _campo(sess, 'payment_status') != 'paid':
        return False, 'Pagamento non ancora completato.', acquisto

    ok, msg = accredita_acquisto(acquisto.pk)
    acquisto.refresh_from_db()
    return ok, msg, acquisto
