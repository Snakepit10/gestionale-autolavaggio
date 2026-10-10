"""Riepilogo delle vendite di prodotti in un periodo, usato dalla scheda
Ordini > Vendita prodotti e dai report giornaliero e di periodo.

Due misure diverse:
- le *vendite prodotti*: carrelli di soli prodotti (Ordine.vendita_prodotti);
- i *prodotti venduti*: tutti gli item di tipo prodotto, compresi quelli
  aggiunti dentro gli ordini di lavaggio.
"""
from decimal import Decimal

from django.db.models import Sum

from .models import ItemOrdine, Ordine


def riepilogo_prodotti(dal, al):
    vendite = (Ordine.objects
               .filter(vendita_prodotti=True, data_ora__date__gte=dal, data_ora__date__lte=al)
               .exclude(stato='annullato'))
    righe = (ItemOrdine.objects
             .filter(servizio_prodotto__tipo='prodotto',
                     ordine__data_ora__date__gte=dal, ordine__data_ora__date__lte=al)
             .exclude(ordine__stato='annullato')
             .select_related('servizio_prodotto', 'ordine'))

    prodotti = {}
    for it in righe:
        voce = prodotti.setdefault(it.servizio_prodotto_id, {
            'prodotto': it.servizio_prodotto, 'quantita': 0, 'incasso': Decimal('0.00'),
            'in_ordini': 0, 'incasso_in_ordini': Decimal('0.00'),
        })
        voce['quantita'] += it.quantita
        voce['incasso'] += it.subtotale
        if not it.ordine.vendita_prodotti:
            voce['in_ordini'] += it.quantita
            voce['incasso_in_ordini'] += it.subtotale
    prodotti = sorted(prodotti.values(), key=lambda v: (-v['incasso'], v['prodotto'].titolo))

    return {
        'n_vendite': vendite.count(),
        'totale_vendite': vendite.aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00'),
        'prodotti': prodotti,
        'pezzi': sum(p['quantita'] for p in prodotti),
        'totale_prodotti': sum((p['incasso'] for p in prodotti), Decimal('0.00')),
        'totale_in_ordini': sum((p['incasso_in_ordini'] for p in prodotti), Decimal('0.00')),
    }
