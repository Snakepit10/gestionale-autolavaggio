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
    """Schermata Garage: card per ogni veicolo + form aggiungi."""
    veicoli = []
    for v in cliente.veicoli.filter(attivo=True):
        veicoli.append({
            'veicolo': v,
            'salute': v.salute_attuale,
            'percorso': stato_percorso(v),
            'n_eventi': v.eventi.count(),
        })
    return render(request, 'clients/garage.html', {
        'cliente': cliente,
        'veicoli': veicoli,
        'form': VeicoloForm(),
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
