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


def serie_trend_segmenti(n_punti: int = 26, passo_giorni: int = 7) -> dict:
    """Serie storiche dei conteggi segmento (automatici + custom attivi).

    Ritorna {labels: [...], serie: [{chiave, nome, colore, semantica,
    valori: [...]}, ...], delta: {chiave: {valore, precedente, delta,
    verso}}} dove delta confronta oggi con ~4 settimane fa e `verso` e'
    'buono'|'cattivo'|'neutro' secondo la semantica del segmento.
    """
    chiave = _chiave_cache(n_punti, passo_giorni)
    cached = cache.get(chiave)
    if cached is not None:
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

    palette_custom = ['#0d6efd', '#6f42c1', '#20c997', '#e83e8c', '#17a2b8']
    serie = []
    for c in CHIAVI_AUTO:
        serie.append({
            'chiave': c, 'nome': SEGMENTI_LABEL[c],
            'colore': COLORI_AUTO[c],
            'semantica': SEGNO_CRESCITA[c],
            'valori': valori[c],
        })
    for i, seg in enumerate(custom):
        serie.append({
            'chiave': seg.chiave, 'nome': seg.nome,
            'colore': palette_custom[i % len(palette_custom)],
            'semantica': 0,
            'valori': valori[seg.chiave],
        })

    # Delta KPI: oggi vs ~4 settimane fa (o il punto piu' vecchio se la
    # serie e' piu' corta).
    idx_confronto = max(0, len(punti) - 1 - max(1, 28 // passo_giorni))
    delta = {}
    for s in serie:
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

    out = {
        'labels': [p.strftime('%d/%m') for p in punti],
        'serie': serie,
        'delta': delta,
        'passo_giorni': passo_giorni,
    }
    cache.set(chiave, out, 60 * 60)
    return out
