"""Integrazione PayPal (REST v2 diretto, niente SDK deprecati).

Flusso capture-on-return:
1. il cliente sceglie PayPal -> creiamo AcquistoMonete + un Order
   PayPal (intent CAPTURE) -> redirect all'approvazione su paypal.com;
2. al ritorno su /app/monete/paypal/ritorno/?token=<order_id> il server
   CATTURA l'ordine e, se COMPLETED, accredita (idempotente, stessa
   accredita_acquisto di Stripe);
3. rete di sicurezza: se il cliente approva ma chiude il browser prima
   del ritorno, il comando `monete_riconcilia` (cron) ritrova gli
   acquisti PayPal rimasti 'creato', interroga l'ordine e cattura se
   APPROVED.

Env: PAYPAL_CLIENT_ID, PAYPAL_SECRET, PAYPAL_BASE_URL
(default sandbox: https://api-m.sandbox.paypal.com; produzione:
https://api-m.paypal.com).
"""
import logging
import time

from django.conf import settings
from django.urls import reverse

import requests

from apps.monete.models import AcquistoMonete
from .acquisti import accredita_acquisto

logger = logging.getLogger('apps.monete.paypal')

# Cache in-process del token OAuth (scade dopo ~9h, teniamo margine)
_token_cache = {'token': '', 'scade_a': 0.0}


def paypal_configurato() -> bool:
    return bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_SECRET)


def _token() -> str:
    """Access token OAuth client-credentials, con cache."""
    if _token_cache['token'] and time.time() < _token_cache['scade_a']:
        return _token_cache['token']
    resp = requests.post(
        f'{settings.PAYPAL_BASE_URL}/v1/oauth2/token',
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_SECRET),
        data={'grant_type': 'client_credentials'},
        timeout=20,
    )
    resp.raise_for_status()
    dati = resp.json()
    _token_cache['token'] = dati['access_token']
    # margine di 60s sulla scadenza dichiarata
    _token_cache['scade_a'] = time.time() + int(dati.get('expires_in', 3600)) - 60
    return _token_cache['token']


def crea_ordine(acquisto: AcquistoMonete, request) -> str:
    """Crea l'Order PayPal e ritorna l'URL di approvazione."""
    return_url = request.build_absolute_uri(reverse('monete_client:paypal-ritorno'))
    cancel_url = (request.build_absolute_uri(
        reverse('monete_client:acquisto-annullato'))
        + f'?acquisto={acquisto.pk}')

    resp = requests.post(
        f'{settings.PAYPAL_BASE_URL}/v2/checkout/orders',
        headers={'Authorization': f'Bearer {_token()}',
                 'Content-Type': 'application/json'},
        json={
            'intent': 'CAPTURE',
            'purchase_units': [{
                'custom_id': str(acquisto.pk),
                'description': f'{acquisto.monete} monete virtuali MasterWash',
                'amount': {'currency_code': 'EUR',
                           'value': f'{acquisto.importo:.2f}'},
            }],
            'application_context': {
                'brand_name': 'Autolavaggio MasterWash',
                'shipping_preference': 'NO_SHIPPING',
                'user_action': 'PAY_NOW',
                'return_url': return_url,
                'cancel_url': cancel_url,
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    ordine = resp.json()

    acquisto.provider_ref = ordine['id']
    acquisto.save(update_fields=['provider_ref', 'aggiornato_il'])

    for link in ordine.get('links', []):
        if link.get('rel') in ('approve', 'payer-action'):
            logger.info('Order PayPal %s creato per acquisto %s.',
                        ordine['id'], acquisto.pk)
            return link['href']
    raise RuntimeError('Risposta PayPal senza link di approvazione.')


def cattura_e_accredita(order_id: str, cliente=None) -> tuple:
    """Cattura l'ordine e accredita se COMPLETED. Ritorna (ok, msg, acquisto).

    `cliente` opzionale: se passato (pagina di ritorno) verifica che
    l'acquisto sia suo; None per la riconciliazione da cron.
    """
    qs = AcquistoMonete.objects.filter(provider='paypal', provider_ref=order_id)
    if cliente is not None:
        qs = qs.filter(cliente=cliente)
    acquisto = qs.first()
    if acquisto is None:
        return False, 'Acquisto non trovato.', None
    if acquisto.stato == 'accreditato':
        return True, 'Acquisto gia\' accreditato.', acquisto

    resp = requests.post(
        f'{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture',
        headers={'Authorization': f'Bearer {_token()}',
                 'Content-Type': 'application/json'},
        timeout=20,
    )
    # 422 ORDER_ALREADY_CAPTURED = cattura precedente riuscita (refresh
    # della pagina di ritorno): verifica lo stato reale dell'ordine.
    if resp.status_code == 422 and 'ALREADY_CAPTURED' in resp.text:
        stato_ordine = 'COMPLETED'
    elif resp.ok:
        stato_ordine = resp.json().get('status', '')
    else:
        logger.warning('Cattura PayPal %s fallita: %s %s',
                       order_id, resp.status_code, resp.text[:300])
        return False, 'Cattura del pagamento non riuscita.', acquisto

    if stato_ordine != 'COMPLETED':
        return False, f'Pagamento non completato (stato {stato_ordine}).', acquisto

    ok, msg = accredita_acquisto(acquisto.pk)
    acquisto.refresh_from_db()
    return ok, msg, acquisto


def stato_ordine(order_id: str) -> str:
    """Stato corrente di un Order PayPal (per la riconciliazione)."""
    resp = requests.get(
        f'{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}',
        headers={'Authorization': f'Bearer {_token()}'},
        timeout=20,
    )
    if not resp.ok:
        return ''
    return resp.json().get('status', '')
