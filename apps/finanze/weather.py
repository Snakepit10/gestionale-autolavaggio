"""Integrazione meteo via Open-Meteo (gratis, no API key).

API: https://open-meteo.com
- Historical: https://archive-api.open-meteo.com/v1/archive (fino a ~2 gg fa)
- Forecast: https://api.open-meteo.com/v1/forecast (con past_days per coprire
  gli ultimi giorni che l'archive non ha ancora consolidato)

Coordinate predefinite: Licata (AG), Sicilia.
Cache locale (Django cache framework) per evitare chiamate ripetute.
"""
import logging
from datetime import date, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request
import json as _json

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Coordinate Licata (Agrigento) - https://www.openstreetmap.org
LICATA_LAT = 37.1037
LICATA_LON = 13.9388

_CACHE_PREFIX = 'weather_licata_v1_'
_CACHE_TTL = 60 * 60 * 24  # 24h


def _http_get_json(url: str, timeout: float = 5.0):
    """GET JSON con timeout. Ritorna dict o None se fallisce."""
    try:
        req = Request(url, headers={'User-Agent': 'gestionale-autolavaggio/1.0'})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return _json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning('Weather API call failed (%s): %s', url, e)
        return None


def fetch_weather_range(data_inizio: date, data_fine: date,
                       lat: float = LICATA_LAT, lon: float = LICATA_LON) -> dict | None:
    """Restituisce dati meteo giornalieri nell'intervallo.

    Output:
        {
            'dates': [date, ...],  # uno per giorno
            'temp_mean': [float, ...],
            'temp_max': [float, ...],
            'temp_min': [float, ...],
            'precipitation': [float mm, ...],
            'sunshine_hours': [float, ...],
        }
    None se l'API e' irraggiungibile.
    """
    cache_key = f'{_CACHE_PREFIX}{lat}_{lon}_{data_inizio.isoformat()}_{data_fine.isoformat()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    today = date.today()
    # Archive API: dati consolidati fino a ~2 gg fa.
    # Forecast con past_days: copre gli ultimi 92 giorni.
    if data_fine < today - timedelta(days=2):
        endpoint = 'https://archive-api.open-meteo.com/v1/archive'
        params = {
            'latitude': lat,
            'longitude': lon,
            'start_date': data_inizio.isoformat(),
            'end_date': data_fine.isoformat(),
            'daily': 'temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum,sunshine_duration',
            'timezone': 'Europe/Rome',
        }
    else:
        # Usa forecast con past_days per coprire intervallo che include oggi/ieri
        past_days = min((today - data_inizio).days, 92)
        future_days = max(0, (data_fine - today).days)
        endpoint = 'https://api.open-meteo.com/v1/forecast'
        params = {
            'latitude': lat,
            'longitude': lon,
            'past_days': past_days,
            'forecast_days': max(1, min(16, future_days + 1)),
            'daily': 'temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum,sunshine_duration',
            'timezone': 'Europe/Rome',
        }

    url = f'{endpoint}?{urlencode(params)}'
    payload = _http_get_json(url)
    if not payload or 'daily' not in payload:
        return None

    daily = payload['daily']
    times = daily.get('time') or []
    dates = []
    for t in times:
        try:
            dates.append(date.fromisoformat(t))
        except ValueError:
            dates.append(None)

    # Filtra solo i giorni nell'intervallo richiesto
    result = {
        'dates': [],
        'temp_mean': [],
        'temp_max': [],
        'temp_min': [],
        'precipitation': [],
        'sunshine_hours': [],
    }
    sunshine_s = daily.get('sunshine_duration') or [None] * len(times)
    for i, d in enumerate(dates):
        if d is None or d < data_inizio or d > data_fine:
            continue
        result['dates'].append(d)
        result['temp_mean'].append(daily.get('temperature_2m_mean', [None])[i])
        result['temp_max'].append(daily.get('temperature_2m_max', [None])[i])
        result['temp_min'].append(daily.get('temperature_2m_min', [None])[i])
        result['precipitation'].append(daily.get('precipitation_sum', [None])[i])
        # sunshine_duration e' in secondi -> ore
        s = sunshine_s[i]
        result['sunshine_hours'].append(round(s / 3600.0, 1) if s is not None else None)

    if not result['dates']:
        return None

    cache.set(cache_key, result, _CACHE_TTL)
    return result


def correlate_revenue_weather(fatturato_per_giorno: list[float], weather: dict) -> dict:
    """Calcola correlazione di Pearson fra fatturato giornaliero e
    variabili meteo. Ritorna dict con coefficienti.

    fatturato_per_giorno: lista parallela a trend_labels (allineata per
    giorno con data_inizio + i giorni).
    weather: output di fetch_weather_range.

    NB: per semplicita non importiamo numpy/scipy. Usiamo formula nativa.
    """
    if not weather or not fatturato_per_giorno:
        return {}

    def _pearson(xs, ys):
        clean = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if len(clean) < 3:
            return None
        n = len(clean)
        sum_x = sum(x for x, _ in clean)
        sum_y = sum(y for _, y in clean)
        mean_x = sum_x / n
        mean_y = sum_y / n
        cov = sum((x - mean_x) * (y - mean_y) for x, y in clean)
        var_x = sum((x - mean_x) ** 2 for x, _ in clean) ** 0.5
        var_y = sum((y - mean_y) ** 2 for _, y in clean) ** 0.5
        if var_x == 0 or var_y == 0:
            return None
        return round(cov / (var_x * var_y), 3)

    return {
        'temp_max': _pearson(weather.get('temp_max') or [], fatturato_per_giorno),
        'precipitation': _pearson(weather.get('precipitation') or [], fatturato_per_giorno),
        'sunshine_hours': _pearson(weather.get('sunshine_hours') or [], fatturato_per_giorno),
    }
