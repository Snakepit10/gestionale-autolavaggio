"""Meta Conversions API: invio server-side dell'evento Schedule.

Si attiva solo se META_PIXEL_ID e META_CAPI_TOKEN sono configurati.
L'event_id arriva dal browser (lo stesso usato dal Pixel via fbq):
Meta deduplica i due invii e tiene il migliore. Un errore qui non
deve MAI far fallire la prenotazione: tutto in try/except largo.
"""
import hashlib
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_API = 'https://graph.facebook.com/v21.0/{pixel_id}/events'


def _sha256(valore):
    return hashlib.sha256(valore.encode('utf-8')).hexdigest()


def _norm_email(email):
    email = (email or '').strip().lower()
    return _sha256(email) if email else None


def _norm_telefono(telefono):
    """E.164 senza '+': cifre con prefisso paese (default Italia)."""
    cifre = ''.join(c for c in (telefono or '') if c.isdigit())
    if not cifre:
        return None
    if cifre.startswith('00'):
        cifre = cifre[2:]
    elif not cifre.startswith('39'):
        cifre = '39' + cifre
    return _sha256(cifre)


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def invia_schedule(request, *, event_id, email, telefono, valore,
                   promo='', fbp=''):
    """Invia l'evento Schedule alla Conversions API (best effort)."""
    pixel_id = settings.META_PIXEL_ID
    token = settings.META_CAPI_TOKEN
    if not pixel_id or not token or not event_id:
        return

    try:
        user_data = {
            'client_ip_address': _client_ip(request),
            'client_user_agent': request.META.get('HTTP_USER_AGENT', ''),
        }
        em = _norm_email(email)
        if em:
            user_data['em'] = [em]
        ph = _norm_telefono(telefono)
        if ph:
            user_data['ph'] = [ph]
        if fbp:
            user_data['fbp'] = fbp

        custom_data = {'value': float(valore or 0), 'currency': 'EUR'}
        if promo:
            custom_data['content_name'] = promo

        payload = {
            'data': [{
                'event_name': 'Schedule',
                'event_time': int(time.time()),
                'event_id': str(event_id)[:64],
                'action_source': 'website',
                'event_source_url': request.build_absolute_uri('/app/servizi/'),
                'user_data': user_data,
                'custom_data': custom_data,
            }],
            'access_token': token,
        }
        # Codice di prova: finche' e' configurato gli eventi compaiono
        # nella scheda "Testa gli eventi" di Gestione eventi
        if settings.META_CAPI_TEST_CODE:
            payload['test_event_code'] = settings.META_CAPI_TEST_CODE

        r = requests.post(_API.format(pixel_id=pixel_id),
                          json=payload, timeout=4)
        if r.status_code != 200:
            logger.warning('Meta CAPI %s: %s', r.status_code, r.text[:300])
    except Exception:
        logger.exception('Meta CAPI: invio Schedule fallito (ignorato)')
