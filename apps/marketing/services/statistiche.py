"""Statistiche campagne: conversioni, tasso, fatturato attribuito.

Tutto in query aggregate (join InvioCampagna -> Ordine con confronto
di date in SQL via F() + timedelta): niente loop N+1 anche su campagne
con centinaia di destinatari.

Definizione di conversione: il cliente contattato ha completato almeno
un lavaggio nella finestra (inviato_il, inviato_il + finestra_giorni].
Il fatturato attribuito somma il totale_finale di TUTTI gli ordini
completati nella finestra (un cliente convertito che torna due volte
conta due ordini di fatturato ma una sola conversione).
"""
from datetime import timedelta

from django.db.models import F, Sum

from apps.marketing.models import Campagna, InvioCampagna


def statistiche_campagna(campagna: Campagna) -> dict:
    """Statistiche complete di una campagna. Query aggregate, no loop."""
    finestra = timedelta(days=campagna.finestra_conversione_giorni)

    base = InvioCampagna.objects.filter(campagna=campagna)
    n_destinatari = base.count()
    n_inviati = base.filter(stato='inviato').count()
    n_falliti = base.filter(stato='fallito').count()
    n_in_coda = base.filter(stato='in_coda').count()
    n_saltati = base.filter(stato='saltato').count()

    # Join invio -> ordini del cliente completati nella finestra.
    # Ogni riga del join e' una coppia (invio, ordine) valida.
    qualificati = base.filter(
        stato='inviato',
        cliente__ordine__stato='completato',
        cliente__ordine__data_ora__gt=F('inviato_il'),
        cliente__ordine__data_ora__lte=F('inviato_il') + finestra,
    )
    # Conversioni: clienti distinti con almeno un ordine in finestra
    n_conversioni = qualificati.values('cliente').distinct().count()
    # Fatturato: somma di tutti gli ordini in finestra (ogni coppia
    # invio-ordine e' unica perche' un cliente ha max 1 invio per
    # campagna -> nessun doppio conteggio dello stesso ordine).
    fatturato = qualificati.aggregate(
        tot=Sum('cliente__ordine__totale_finale'))['tot'] or 0

    tasso = (n_conversioni / n_inviati * 100) if n_inviati else 0.0

    return {
        'n_destinatari': n_destinatari,
        'n_inviati': n_inviati,
        'n_falliti': n_falliti,
        'n_in_coda': n_in_coda,
        'n_saltati': n_saltati,
        'n_conversioni': n_conversioni,
        'tasso_conversione': tasso,
        'fatturato': float(fatturato),
    }


def statistiche_per_segmento() -> list[dict]:
    """Aggregato per segmento di origine su tutte le campagne.

    Ritorna una riga per segmento con inviati, conversioni, tasso e
    fatturato cumulati. Usato nella pagina campagne per capire quale
    segmento risponde meglio.
    """
    out = {}
    for campagna in Campagna.objects.exclude(segmento_origine=''):
        stats = statistiche_campagna(campagna)
        seg = campagna.segmento_origine
        if seg not in out:
            out[seg] = {
                'segmento': seg, 'n_inviati': 0,
                'n_conversioni': 0, 'fatturato': 0.0,
            }
        out[seg]['n_inviati'] += stats['n_inviati']
        out[seg]['n_conversioni'] += stats['n_conversioni']
        out[seg]['fatturato'] += stats['fatturato']
    righe = []
    for seg, r in out.items():
        r['tasso_conversione'] = (
            r['n_conversioni'] / r['n_inviati'] * 100 if r['n_inviati'] else 0.0
        )
        righe.append(r)
    righe.sort(key=lambda r: -r['tasso_conversione'])
    return righe
