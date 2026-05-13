"""Lag analysis: correlazioni con sfasamento temporale.

- Meteo: come la pioggia/temperatura di N giorni fa influenza il
  fatturato di oggi.
- Festivita: il fatturato si concentra su pre-festivo? Post-festivo?
  Festivita stesse?

Tutto in puro Python (no scipy/pandas).
"""
from __future__ import annotations
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Pearson + lag correlation
# ---------------------------------------------------------------------------

def _pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mean_x = sum(p[0] for p in pairs) / n
    mean_y = sum(p[1] for p in pairs) / n
    cov = sum((p[0] - mean_x) * (p[1] - mean_y) for p in pairs)
    var_x = sum((p[0] - mean_x) ** 2 for p in pairs) ** 0.5
    var_y = sum((p[1] - mean_y) ** 2 for p in pairs) ** 0.5
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / (var_x * var_y), 3)


def lag_correlation(driver_series: list[float | None],
                    revenue_series: list[float | None],
                    lag: int) -> float | None:
    """Correlazione fra revenue di oggi e driver di N giorni fa.

    lag=0  : stesso giorno
    lag=1  : revenue[i] vs driver[i-1] (driver di ieri)
    lag=-1 : revenue[i] vs driver[i+1] (effetto anticipato, raro)
    """
    if len(driver_series) != len(revenue_series):
        return None
    n = len(driver_series)
    if lag == 0:
        return _pearson(driver_series, revenue_series)
    if lag > 0:
        if lag >= n:
            return None
        return _pearson(driver_series[:n - lag], revenue_series[lag:])
    if lag < 0:
        k = -lag
        if k >= n:
            return None
        return _pearson(driver_series[k:], revenue_series[:n - k])
    return None


def lag_correlation_series(driver_series: list[float | None],
                           revenue_series: list[float | None],
                           lags: list[int]) -> list[dict]:
    """Ritorna [{lag: int, corr: float|None}, ...] per ogni lag richiesto."""
    return [
        {'lag': lag, 'corr': lag_correlation(driver_series, revenue_series, lag)}
        for lag in lags
    ]


# ---------------------------------------------------------------------------
# Italian holidays
# ---------------------------------------------------------------------------

def easter_date(year: int) -> date:
    """Calcola la data di Pasqua per un anno (algoritmo Meeus/Jones/Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def italian_holidays_for_year(year: int) -> dict[date, str]:
    """Festivita italiane nazionali + patrono Licata (14 settembre)."""
    pasqua = easter_date(year)
    pasquetta = pasqua + timedelta(days=1)
    return {
        date(year, 1, 1): 'Capodanno',
        date(year, 1, 6): 'Epifania',
        pasqua: 'Pasqua',
        pasquetta: 'Pasquetta',
        date(year, 4, 25): 'Liberazione',
        date(year, 5, 1): 'Festa del Lavoro',
        date(year, 6, 2): 'Repubblica',
        date(year, 8, 15): 'Ferragosto',
        date(year, 9, 14): "Sant'Angelo (patrono Licata)",
        date(year, 11, 1): 'Tutti i Santi',
        date(year, 12, 8): 'Immacolata',
        date(year, 12, 25): 'Natale',
        date(year, 12, 26): 'Santo Stefano',
    }


def italian_holidays_in_range(data_inizio: date, data_fine: date) -> dict[date, str]:
    out = {}
    for year in range(data_inizio.year, data_fine.year + 1):
        for d, name in italian_holidays_for_year(year).items():
            if data_inizio <= d <= data_fine:
                out[d] = name
    return out


# ---------------------------------------------------------------------------
# Holiday lag analysis
# ---------------------------------------------------------------------------

def classify_day(d: date, holidays: dict[date, str]) -> str:
    """Categorizza un giorno: festivo, ponte, pre/post-festivo, weekend, feriale."""
    if d in holidays:
        return 'festivo'
    yesterday = d - timedelta(days=1)
    tomorrow = d + timedelta(days=1)
    if tomorrow in holidays:
        return 'pre_festivo'
    if yesterday in holidays:
        return 'post_festivo'
    wd = d.weekday()  # 0=lun..6=dom
    if wd == 5:
        return 'sabato'
    if wd == 6:
        return 'domenica'
    return 'feriale'


CATEGORY_LABELS = {
    'feriale': 'Feriale (lun-ven)',
    'sabato': 'Sabato',
    'domenica': 'Domenica',
    'pre_festivo': 'Pre-festivo',
    'festivo': 'Festivo',
    'post_festivo': 'Post-festivo',
}


def analyze_holidays(data_inizio: date, data_fine: date,
                     fatturato_per_giorno: list[float]) -> dict:
    """Aggrega il fatturato per categoria giorno e calcola medie + max.

    Ritorna:
        {
            'categories': [{'key': 'feriale', 'label': ..., 'count': N,
                            'totale': X, 'media': X/N, 'max': Y}, ...],
            'holidays_list': [{'date': ..., 'name': ..., 'fatturato': ...}, ...]
                ordinato per data (solo giorni festivi nel periodo)
            'lag_corr': lag correlation of revenue vs "is_holiday" indicator
                lags [-3..+3] per vedere effetto anticipato/posticipato
        }
    """
    holidays = italian_holidays_in_range(data_inizio, data_fine)
    cats = {k: {'count': 0, 'totale': 0.0, 'max': 0.0} for k in CATEGORY_LABELS}

    d = data_inizio
    is_holiday_series = []
    for i in range(len(fatturato_per_giorno)):
        cur = data_inizio + timedelta(days=i)
        cat = classify_day(cur, holidays)
        cats[cat]['count'] += 1
        cats[cat]['totale'] += fatturato_per_giorno[i]
        cats[cat]['max'] = max(cats[cat]['max'], fatturato_per_giorno[i])
        is_holiday_series.append(1.0 if cur in holidays else 0.0)

    categories_out = []
    for k in ['feriale', 'sabato', 'domenica', 'pre_festivo', 'festivo', 'post_festivo']:
        c = cats[k]
        media = c['totale'] / c['count'] if c['count'] > 0 else 0.0
        categories_out.append({
            'key': k,
            'label': CATEGORY_LABELS[k],
            'count': c['count'],
            'totale': round(c['totale'], 2),
            'media': round(media, 2),
            'max': round(c['max'], 2),
        })

    # Lista festivita nel periodo con fatturato
    holidays_list = []
    for hd, name in sorted(holidays.items()):
        idx = (hd - data_inizio).days
        if 0 <= idx < len(fatturato_per_giorno):
            holidays_list.append({
                'date': hd,
                'name': name,
                'fatturato': round(fatturato_per_giorno[idx], 2),
            })

    # Lag correlation: effetto della festivita sul giorno (lag 0),
    # giorni dopo (lag 1, 2, 3) e prima (lag -1, -2, -3)
    lag_corr = lag_correlation_series(
        is_holiday_series, fatturato_per_giorno, list(range(-3, 4))
    )

    return {
        'categories': categories_out,
        'holidays_list': holidays_list,
        'lag_corr': lag_corr,
    }
