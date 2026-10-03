"""Report delle conversioni web (promo/campagne Meta).

Le prenotazioni arrivate dalle landing portano in nota_cliente un
marcatore scritto dal wizard di prenotazione:

    [PROMO garanzia_pioggia | facebook/cpc/nome-campagna]
    [PROMO garanzia_pioggia]            (senza UTM)

Qui si aggregano per (promo, campagna) con funnel degli stati e
fatturato reale degli ordini collegati. Nessun campo nuovo: si
leggono i marcatori gia' salvati, quindi il report vale anche
retroattivamente.
"""
import re
from decimal import Decimal

from django.utils import timezone

from apps.prenotazioni.models import Prenotazione

MARKER_RE = re.compile(r'\[PROMO ([a-z0-9_-]+)(?:\s*\|\s*([^\]]+))?\]',
                       re.IGNORECASE)

# Ordine di presentazione del funnel
_STATI_FUNNEL = ['in_attesa', 'confermata', 'completata', 'annullata',
                 'no_show']


def estrai_marker(nota):
    """(promo, campagna) dal marcatore, o None se assente."""
    m = MARKER_RE.search(nota or '')
    if not m:
        return None
    promo = m.group(1).lower()
    campagna = (m.group(2) or '').strip() or 'diretto'
    return promo, campagna


def report_conversioni_web(dal, al):
    """Gruppi (promo, campagna) con funnel e fatturato nel periodo.

    `dal`/`al`: date incluse, filtrate su Prenotazione.creata_il.
    """
    prenotazioni = (
        Prenotazione.objects
        .filter(creata_il__date__gte=dal, creata_il__date__lte=al,
                nota_cliente__icontains='[PROMO ')
        .select_related('cliente', 'ordine', 'slot')
        .prefetch_related('servizi')
        .order_by('-creata_il')
    )

    gruppi = {}
    for p in prenotazioni:
        marker = estrai_marker(p.nota_cliente)
        if not marker:
            continue
        gruppo = gruppi.setdefault(marker, {
            'promo': marker[0],
            'campagna': marker[1],
            'prenotazioni': [],
            'stati': {s: 0 for s in _STATI_FUNNEL},
            'n_ordini': 0,
            'fatturato': Decimal('0'),
            'incassato': Decimal('0'),
        })
        gruppo['prenotazioni'].append(p)
        if p.stato in gruppo['stati']:
            gruppo['stati'][p.stato] += 1
        if p.ordine_id and p.ordine.stato != 'annullato':
            gruppo['n_ordini'] += 1
            gruppo['fatturato'] += p.ordine.totale_finale or Decimal('0')
            gruppo['incassato'] += p.ordine.importo_pagato or Decimal('0')

    risultato = []
    for gruppo in gruppi.values():
        n = len(gruppo['prenotazioni'])
        gruppo['n_richieste'] = n
        # "convertite" = confermate o gia' completate
        gruppo['n_confermate'] = (gruppo['stati']['confermata']
                                  + gruppo['stati']['completata'])
        gruppo['pct_confermate'] = round(
            gruppo['n_confermate'] * 100 / n) if n else 0
        risultato.append(gruppo)

    risultato.sort(key=lambda g: (-g['n_richieste'], g['campagna']))
    totale = {
        'n_richieste': sum(g['n_richieste'] for g in risultato),
        'n_confermate': sum(g['n_confermate'] for g in risultato),
        'n_ordini': sum(g['n_ordini'] for g in risultato),
        'fatturato': sum((g['fatturato'] for g in risultato), Decimal('0')),
        'incassato': sum((g['incassato'] for g in risultato), Decimal('0')),
    }
    return risultato, totale
