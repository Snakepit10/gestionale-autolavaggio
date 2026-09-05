"""Voto e numero recensioni Google della scheda MasterWash.

Usa la Google Places API (Place Details, campi rating e
user_ratings_total). Attivazione:
1. Google Cloud Console -> abilita "Places API" -> crea API key;
2. trova il Place ID della scheda (https://developers.google.com/maps/
   documentation/places/web-service/place-id oppure cercando
   "place id finder");
3. su Railway imposta GOOGLE_PLACES_API_KEY e GOOGLE_PLACE_ID.

Senza chiave la funzione ritorna None e la landing mostra solo il link
"Recensioni Google" senza numeri (mai numeri inventati). Cache 24h:
una chiamata al giorno, costo praticamente zero.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_KEY = 'google_reviews_rating'


def rating_google() -> dict | None:
    """{'rating': 4.9, 'totale': 127} oppure None se non configurato
    o non disponibile."""
    api_key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '')
    place_id = getattr(settings, 'GOOGLE_PLACE_ID', '')
    if not api_key or not place_id:
        return None

    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached or None  # {} cached = errore recente, non martellare

    out = None
    try:
        r = requests.get(
            'https://maps.googleapis.com/maps/api/place/details/json',
            params={'place_id': place_id,
                    'fields': 'rating,user_ratings_total',
                    'key': api_key},
            timeout=6,
        )
        dati = r.json() if r.status_code < 400 else {}
        ris = dati.get('result') or {}
        if ris.get('rating'):
            out = {'rating': ris['rating'],
                   'totale': ris.get('user_ratings_total', 0)}
        else:
            logger.warning('Places API senza rating: %s',
                           dati.get('status', r.status_code))
    except requests.RequestException as e:
        logger.warning('Places API errore: %s', e)

    # 24h se ok, 1h se errore (riprova presto ma senza raffiche)
    cache.set(_CACHE_KEY, out or {}, 60 * 60 * 24 if out else 60 * 60)
    return out
