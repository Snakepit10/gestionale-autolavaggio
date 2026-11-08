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

from django.db.models import Count, Sum

ZERO = Decimal('0.00')


def ordini_di_lavaggio(ordini):
    """Lavaggi = ordini con almeno un servizio, senza le vendite di soli
    prodotti. Gli accessi in abbonamento (servizio a 0 euro) contano; la
    vendita dell'abbonamento (ordine senza righe) no."""
    return ordini.filter(vendita_prodotti=False, items__servizio_prodotto__tipo='servizio').distinct()


def cicli_servito_portale(dal, al):
    """Cicli dei portali usati dal servito, come l'abbinamento del report
    giornata: un ciclo per pezzo dei servizi con programmi portale, tranne
    quelli chiusi senza portale, piu' gli abbinati a mano dei servizi
    senza programmi."""
    from apps.ordini.models import ItemOrdine

    from ..models import AbbinamentoPortale
    con_programmi = (ItemOrdine.objects.filter(ordine__data_ora__date__gte=dal, ordine__data_ora__date__lte=al,
                                               senza_portale__isnull=True)
                     .exclude(servizio_prodotto__programmi_portale='').exclude(ordine__stato='annullato')
                     .aggregate(q=Sum('quantita'))['q'] or 0)
    a_mano = (AbbinamentoPortale.objects.filter(item__ordine__data_ora__date__gte=dal, item__ordine__data_ora__date__lte=al,
                                                item__servizio_prodotto__programmi_portale='')
              .exclude(item__ordine__stato='annullato').aggregate(n=Count('id'))['n'])
    return int(con_programmi) + a_mano


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

    lavaggi = ordini_di_lavaggio(ordini)
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
        'cicli_servito': cicli_servito_portale(dal, al),
        'ordini_lavaggio': lavaggi.count(),
        'lavaggi_abbonamento': lavaggi.filter(origine='abbonamento').count(),
        'rp': rp,
    }
