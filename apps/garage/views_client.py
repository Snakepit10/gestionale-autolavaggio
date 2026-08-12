"""Viste lato CLIENTE del Garage (prefisso /app/garage/).

Riusa il decorator _cliente_required dell'app monete (stesso flusso di
login e stesso inject del Cliente). Ogni veicolo viene sempre caricato
filtrando per cliente: mai esporre veicoli altrui (404).
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from apps.monete.views_client import _cliente_required

from .forms import VeicoloForm
from .models import Veicolo
from .services.percorso import stato_percorso


def _veicolo_del_cliente(cliente, pk) -> Veicolo:
    veicolo = Veicolo.objects.filter(
        pk=pk, cliente=cliente, attivo=True).first()
    if veicolo is None:
        raise Http404
    return veicolo


@_cliente_required
def garage(request, cliente):
    """Schermata Garage: card per ogni veicolo + form aggiungi +
    spiegazione di come funziona (coi numeri veri della config)."""
    from .models import ImpostazioniGarage, Percorso

    veicoli = []
    for v in cliente.veicoli.filter(attivo=True):
        veicoli.append({
            'veicolo': v,
            'salute': v.salute_attuale,
            'percorso': stato_percorso(v),
            'n_eventi': v.eventi.count(),
        })

    cfg = ImpostazioniGarage.get_solo()
    std = Percorso.standard()
    livelli_std = list(std.livelli.order_by('numero')) if std else []
    premio_totale = sum(l.premio_gettoni for l in livelli_std)
    # Traguardi streak ordinati per la spiegazione ("5 di fila = +1...")
    traguardi = sorted(
        ((int(k), v) for k, v in (cfg.streak_traguardi or {}).items()),
        key=lambda x: x[0])

    return render(request, 'clients/garage.html', {
        'cliente': cliente,
        'veicoli': veicoli,
        'form': VeicoloForm(),
        'cfg': cfg,
        'livelli_std': livelli_std,
        'premio_totale': premio_totale,
        'traguardi_streak': traguardi,
    })


@_cliente_required
def veicolo_aggiungi(request, cliente):
    if request.method != 'POST':
        return redirect('garage_client:garage')
    from .models import ImpostazioniGarage, Percorso

    form = VeicoloForm(request.POST, cliente=cliente)
    if not form.is_valid():
        # Un solo messaggio leggibile: il form e' corto, l'errore e'
        # quasi sempre sulla targa.
        errori = '; '.join(
            e for errs in form.errors.values() for e in errs)
        messages.error(request, errori or 'Controlla i dati del veicolo.')
        return redirect('garage_client:garage')

    veicolo = form.save(commit=False)
    veicolo.cliente = cliente
    veicolo.percorso = Percorso.standard()
    veicolo.punteggio_salute = ImpostazioniGarage.get_solo().punteggio_iniziale
    veicolo.save()
    messages.success(
        request,
        f'Veicolo {veicolo.targa} aggiunto al garage! Il suo percorso '
        f'parte dal Livello 1: ogni servizio lo fara\' salire.')
    return redirect('garage_client:garage')


@_cliente_required
def scheda(request, cliente, pk):
    """Scheda veicolo: salute con trend, livello con checklist, badge,
    prossimo servizio consigliato, trattamenti e accesso al libretto."""
    from .models import ImpostazioniGarage
    from .services.scadenze import trattamenti_veicolo

    veicolo = _veicolo_del_cliente(cliente, pk)
    cfg = ImpostazioniGarage.get_solo()
    stato = stato_percorso(veicolo)
    trattamenti = trattamenti_veicolo(veicolo)

    # Trend salute: se c'e' un servizio dentro l'ultima finestra di
    # decadimento la salute sta tenendo/salendo, altrimenti sta
    # scendendo di decadimento_punti ogni decadimento_giorni.
    ultimo_evento = veicolo.eventi.order_by('-data').first()
    giorni_da_ultimo = None
    if ultimo_evento:
        giorni_da_ultimo = (timezone_now_date()
                            - localdate(ultimo_evento.data)).days
    salute = veicolo.salute_attuale
    if salute >= 100:
        trend = 'stabile'
    elif giorni_da_ultimo is not None and giorni_da_ultimo <= cfg.decadimento_giorni:
        trend = 'su'
    else:
        trend = 'giu'

    # Prossimo consigliato: primo item mancante del livello corrente;
    # nel Club il trattamento da rinnovare (o in scadenza) piu' vicino.
    consigliato = None
    consigliato_scadenza = None
    if stato.nel_club:
        urgenti = [t for t in trattamenti if t.stato != 'attivo']
        if urgenti:
            consigliato_scadenza = urgenti[0]
        elif trattamenti:
            consigliato_scadenza = trattamenti[0]
    else:
        consigliato = stato.prossimo_item()

    return render(request, 'clients/garage_scheda.html', {
        'cliente': cliente,
        'veicolo': veicolo,
        'salute': salute,
        'trend': trend,
        'giorni_da_ultimo': giorni_da_ultimo,
        'cfg': cfg,
        'stato': stato,
        'consigliato': consigliato,
        'consigliato_scadenza': consigliato_scadenza,
        'trattamenti': trattamenti,
        'badges': veicolo.badges.all(),
        'n_eventi': veicolo.eventi.count(),
    })


def timezone_now_date():
    from django.utils import timezone
    return timezone.localtime(timezone.now()).date()


def localdate(dt):
    from django.utils import timezone
    return timezone.localtime(dt).date()


@_cliente_required
def percorso(request, cliente, pk):
    """Vista dei livelli del percorso con stato e premi."""
    from .models import PremioVeicolo

    veicolo = _veicolo_del_cliente(cliente, pk)
    stato = stato_percorso(veicolo)
    premiati = set(
        PremioVeicolo.objects.filter(
            veicolo=veicolo, chiave__startswith='livello:')
        .values_list('chiave', flat=True))
    livelli = [
        {'ls': ls, 'premiato': f'livello:{ls.livello.pk}' in premiati}
        for ls in stato.livelli
    ]
    return render(request, 'clients/garage_percorso.html', {
        'cliente': cliente,
        'veicolo': veicolo,
        'stato': stato,
        'livelli': livelli,
        'salute': veicolo.salute_attuale,
    })


@_cliente_required
def libretto(request, cliente, pk):
    """Timeline cronologica del libretto, con filtro per tipo servizio."""
    from .models import TipoServizioGarage

    veicolo = _veicolo_del_cliente(cliente, pk)
    eventi = (veicolo.eventi
              .select_related('tipo_servizio', 'registrato_da')
              .order_by('-data'))

    tipo_sel = None
    slug = request.GET.get('tipo', '')
    if slug:
        tipo_sel = TipoServizioGarage.objects.filter(slug=slug).first()
        if tipo_sel is not None:
            eventi = eventi.filter(tipo_servizio=tipo_sel)

    tipi_presenti = (TipoServizioGarage.objects
                     .filter(eventi__veicolo=veicolo).distinct()
                     .order_by('ordine'))
    return render(request, 'clients/garage_libretto.html', {
        'cliente': cliente,
        'veicolo': veicolo,
        'salute': veicolo.salute_attuale,
        'eventi': eventi[:200],
        'tipi_presenti': tipi_presenti,
        'tipo_sel': tipo_sel,
    })


@_cliente_required
def veicolo_elimina(request, cliente, pk):
    """Rimuove un veicolo dal garage.

    Se ha storico sul libretto viene solo disattivato (lo storico
    resta nel DB, il cliente non lo vede piu'); se e' vuoto si elimina
    davvero.
    """
    if request.method != 'POST':
        return redirect('garage_client:garage')
    veicolo = _veicolo_del_cliente(cliente, pk)
    targa = veicolo.targa
    if veicolo.eventi.exists():
        veicolo.attivo = False
        veicolo.save(update_fields=['attivo'])
    else:
        veicolo.delete()
    messages.success(request, f'Veicolo {targa} rimosso dal garage.')
    return redirect('garage_client:garage')
