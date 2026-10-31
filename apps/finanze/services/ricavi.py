"""Ricavi di un periodo per canale: le stesse fonti del report periodo,
usate anche dal conto economico della contabilita', cosi' i numeri non
divergono. Importi lordi (IVA compresa).

- servito: ordini del periodo (non annullati, anche non pagati) senza
  la vendita prodotti;
- prodotti: carrelli di soli prodotti + prodotti dentro gli ordini di
  lavaggio;
- portali e cambia gettoni: vendita delle casse automatiche (il
  registratore fiscale del servito resta fuori: sono gli stessi ordini);
- monete online: acquisti di monete virtuali pagati (app)."""
from decimal import Decimal

from django.db.models import Sum

ZERO = Decimal('0.00')


def ricavi_periodo(dal, al):
    from apps.monete.models import AcquistoMonete
    from apps.ordini.models import Ordine
    from apps.ordini.vendite import riepilogo_prodotti

    from ..models import ChiusuraCassaAutomatica

    ordini = (Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al)
              .exclude(stato='annullato'))
    servito_ordinato = ordini.aggregate(s=Sum('totale_finale'))['s'] or ZERO
    rp = riepilogo_prodotti(dal, al)
    prodotti = rp['totale_vendite'] + rp['totale_in_ordini']

    portali = cambia_gettoni = registratore = ZERO
    washcycles = 0
    for c in ChiusuraCassaAutomatica.objects.filter(data__gte=dal, data__lte=al).select_related('cassa'):
        if c.cassa.modalita_registratore:
            registratore += c.incasso_totale
        elif c.cassa.tracking_washcycles:
            portali += c.vendita_totale
            washcycles += c.wash_cycles or 0
        else:
            cambia_gettoni += c.vendita_totale

    monete_online = (AcquistoMonete.objects
                     .filter(stato__in=('pagato', 'accreditato'), creato_il__date__gte=dal, creato_il__date__lte=al)
                     .aggregate(s=Sum('importo'))['s'] or ZERO)
    return {
        'servito': servito_ordinato - prodotti,
        'prodotti': prodotti,
        'portali': portali,
        'cambia_gettoni': cambia_gettoni,
        'monete_online': monete_online,
        'servito_ordinato': servito_ordinato,
        'registratore': registratore,
        'washcycles': washcycles,
        'ordini_lavaggio': ordini.filter(vendita_prodotti=False).count(),
        'rp': rp,
    }
