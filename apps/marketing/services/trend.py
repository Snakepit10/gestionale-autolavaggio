"""Trend storici dei segmenti clienti (KPI).

La segmentazione dipende SOLO dallo storico ordini completati, quindi
il passato e' ricostruibile esattamente: per ogni data campione si
rifanno le statistiche "come se fosse quel giorno"
(statistiche_clienti(alla_data=...) + segmenta_clienti(adesso=...)).
Niente snapshot da accumulare: i trend sono disponibili da subito su
tutta la storia.

Costo: un punto = 1 query aggregata + loop Python. Con 26 punti
settimanali e' qualche secondo la prima volta, poi cache (1 ora,
invalidata da cambi di soglie o di segmenti personalizzati).
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.marketing.models import ImpostazioniMarketing, SegmentoPersonalizzato

from .segmentazione import (SEGMENTI_LABEL, filtra_segmento_personalizzato,
                            segmenta_clienti, statistiche_clienti)

CHIAVI_AUTO = ('attivi', 'rallentamento', 'dormienti', 'one_shot')

# Periodi selezionabili dal grafico: settimane totali -> (punti, passo
# in giorni). Oltre l'anno il passo diventa quindicinale per tenere il
# grafico leggibile (e il costo di calcolo costante).
PERIODI_TREND = {
    13: {'punti': 13, 'passo': 7, 'label': '3 mesi'},
    26: {'punti': 26, 'passo': 7, 'label': '6 mesi'},
    52: {'punti': 52, 'passo': 7, 'label': '1 anno'},
    104: {'punti': 52, 'passo': 14, 'label': '2 anni'},
}
PERIODO_DEFAULT = 26

# Semantica KPI: crescere e' un bene o un male?
# +1 = crescita positiva (verde), -1 = crescita negativa (rosso),
# 0 = neutra (i segmenti personalizzati: dipende dal filtro).
SEGNO_CRESCITA = {
    'attivi': +1,
    'rallentamento': -1,
    'dormienti': -1,
    'one_shot': -1,   # tanti one-shot = nuovi clienti che non tornano
}

COLORI_AUTO = {
    'attivi': '#198754',
    'rallentamento': '#d4a017',
    'dormienti': '#dc3545',
    'one_shot': '#6c757d',
}


def _chiave_cache(n_punti, passo_giorni):
    """La cache si invalida da sola se cambiano soglie o segmenti
    custom (timestamp nel nome della chiave) o al cambio di giorno."""
    cfg = ImpostazioniMarketing.get_solo()
    ultimo_custom = (SegmentoPersonalizzato.objects
                     .order_by('-aggiornato_il')
                     .values_list('aggiornato_il', flat=True).first())
    oggi = timezone.localtime(timezone.now()).date()
    tag_custom = (ultimo_custom.strftime('%Y%m%d%H%M%S')
                  if ultimo_custom else 'none')
    return (f'mkt_trend_segmenti:{oggi}:{n_punti}:{passo_giorni}:'
            f'{cfg.aggiornato_il:%Y%m%d%H%M%S}:{tag_custom}')


def calcola_delta(trend: dict, giorni_confronto: int = 30) -> dict:
    """Delta KPI: oggi vs ~giorni_confronto fa, sulla serie gia'
    calcolata (nessuna query). `verso` = 'buono'|'cattivo'|'neutro'
    secondo la semantica del segmento."""
    passo = trend.get('passo_giorni', 7)
    n_labels = len(trend['labels'])
    indietro = max(1, round(giorni_confronto / passo))
    idx_confronto = max(0, n_labels - 1 - indietro)
    delta = {}
    for s in trend['serie']:
        v_oggi = s['valori'][-1]
        v_prima = s['valori'][idx_confronto]
        d = v_oggi - v_prima
        if s['semantica'] == 0 or d == 0:
            verso = 'neutro'
        elif (d > 0) == (s['semantica'] > 0):
            verso = 'buono'
        else:
            verso = 'cattivo'
        delta[s['chiave']] = {
            'valore': v_oggi, 'precedente': v_prima,
            'delta': d, 'verso': verso,
        }
    return delta


def serie_trend_segmenti(n_punti: int = 26, passo_giorni: int = 7,
                         giorni_confronto: int = 30) -> dict:
    """Serie storiche dei conteggi segmento (automatici + custom attivi).

    Ritorna {labels, serie: [{chiave, nome, colore, tipo, semantica,
    valori}], delta, passo_giorni, generato_il}. Le serie sono in cache
    1h; il delta viene ricalcolato a ogni chiamata sulla finestra
    `giorni_confronto` richiesta (7/30/90...).
    """
    chiave = _chiave_cache(n_punti, passo_giorni)
    cached = cache.get(chiave)
    if cached is not None:
        cached = dict(cached)
        cached['delta'] = calcola_delta(cached, giorni_confronto)
        return cached

    cfg = ImpostazioniMarketing.get_solo()
    custom = list(SegmentoPersonalizzato.objects.filter(attivo=True))
    ora = timezone.localtime(timezone.now())

    # Punti dal piu' vecchio a oggi (l'ultimo punto e' ADESSO)
    punti = [ora - timedelta(days=passo_giorni * i)
             for i in range(n_punti - 1, -1, -1)]

    valori = {c: [] for c in CHIAVI_AUTO}
    for seg in custom:
        valori[seg.chiave] = []

    for punto in punti:
        # Ultimo punto = presente: niente filtro alla_data (piu' veloce
        # e identico ai numeri delle card).
        alla_data = None if punto is punti[-1] else punto
        stats = statistiche_clienti(alla_data=alla_data)
        ris = segmenta_clienti(cfg=cfg, stats=stats, adesso=punto)
        for c in CHIAVI_AUTO:
            valori[c].append(len(ris.get(c)))
        for seg in custom:
            valori[seg.chiave].append(
                len(filtra_segmento_personalizzato(seg, stats)))

    palette_custom = ['#0d6efd', '#6f42c1', '#20c997', '#e83e8c',
                      '#17a2b8', '#795548', '#ff7043', '#5c6bc0',
                      '#8bc34a', '#00897b', '#c2185b']
    serie = []
    for c in CHIAVI_AUTO:
        serie.append({
            'chiave': c, 'nome': SEGMENTI_LABEL[c],
            'colore': COLORI_AUTO[c],
            'tipo': 'auto',
            'semantica': SEGNO_CRESCITA[c],
            'valori': valori[c],
        })
    for i, seg in enumerate(custom):
        serie.append({
            'chiave': seg.chiave, 'nome': seg.nome,
            'colore': palette_custom[i % len(palette_custom)],
            'tipo': 'custom',
            'semantica': 0,
            'valori': valori[seg.chiave],
        })

    # Oltre l'anno di storia le date si ripetono: aggiungi l'anno
    fmt = '%d/%m/%y' if n_punti * passo_giorni > 360 else '%d/%m'
    out = {
        'labels': [p.strftime(fmt) for p in punti],
        'serie': serie,
        'passo_giorni': passo_giorni,
        'generato_il': timezone.localtime(timezone.now()).strftime('%d/%m %H:%M'),
    }
    cache.set(chiave, out, 60 * 60)
    out = dict(out)
    out['delta'] = calcola_delta(out, giorni_confronto)
    return out
