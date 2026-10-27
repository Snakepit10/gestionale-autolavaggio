"""Spazzole <-> servito per gli operatori (scheda "Distinta", sezione Ordini).

Le transazioni WashTec arrivano da sole (import automatico, vedi
services/import_automatico.py; "Aggiorna ora" per non aspettare il giro
successivo). Gli operatori confermano le proposte di abbinamento e
sistemano i lavaggi rimasti scoperti; residuo, soldi e finestra di
chiusura restano nel report giornata del titolare.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.auth_system.sezioni import ha_accesso

from .services import abbinamento_portali, import_automatico
from .views_chiusura import _data, giornata_confermata, puo_modificare


def fa_spazzole(user):
    """Conferma gli abbinamenti spazzole <-> servito e avvia l'import."""
    return ha_accesso(user, 'spazzole_servito') or ha_accesso(user, 'finanze')


def _chiusura(giorno):
    # La finestra di chiusura serve solo al residuo: per gli abbinamenti
    # del servito conta il giorno, quindi qui non si salva nulla.
    from .views import _chiusura_portali
    return _chiusura_portali(giorno)[0]


def riepilogo_spazzole(giorno):
    """Righe del servito con uno stato semplice: 'ok' (abbinato o chiuso
    senza portale), 'proposto' (proposte da confermare), 'da_sistemare'."""
    r = abbinamento_portali.riepilogo(_chiusura(giorno), con_proposte=True)
    for riga in r['righe']:
        if riga['senza_portale'] or not riga['slot_liberi']:
            riga['stato'] = 'ok'
        elif riga['proposte'] and not riga['scoperti']:
            riga['stato'] = 'proposto'
        else:
            riga['stato'] = 'da_sistemare'
    conta = lambda s: sum(1 for riga in r['righe'] if riga['stato'] == s)
    return {'righe': r['righe'], 'n_proposte': r['n_proposte'],
            'n_ok': conta('ok'), 'n_proposto': conta('proposto'), 'n_da_sistemare': conta('da_sistemare')}


@login_required
def spazzole_giornata(request):
    if not fa_spazzole(request.user):
        raise PermissionDenied
    giorno = _data(request)
    if request.method == 'POST':
        torna = f"{reverse('ordini:spazzole-giornata')}?data={giorno:%Y-%m-%d}"
        if not puo_modificare(request.user, giorno):
            messages.error(request, 'La giornata è già stata confermata: chiedi al titolare di sbloccarla.')
        else:
            _azione(request, giorno)
        return redirect(torna + (request.POST.get('ancora') or ''))

    oggi = timezone.localdate()
    return render(request, 'ordini/spazzole_giornata.html', {
        'data': giorno, 'oggi': oggi,
        'data_prev': giorno - timedelta(days=1),
        'data_next': giorno + timedelta(days=1) if giorno < oggi else None,
        'spazzole': riepilogo_spazzole(giorno),
        'modificabile': puo_modificare(request.user, giorno),
        'confermata': giornata_confermata(giorno),
        'stato_washtec': import_automatico.stato(),
    })


def _intero(valore):
    try:
        return int(valore)
    except (TypeError, ValueError):
        return None


def _azione(request, giorno):
    p = request.POST
    azione = p.get('azione')
    chiusura = _chiusura(giorno)
    item_id, tx_id = _intero(p.get('item_id')), _intero(p.get('transazione_id'))
    if azione == 'conferma':
        n = abbinamento_portali.conferma_proposte(chiusura, request.user)
        messages.success(request, f'{n} abbinament{"o confermato" if n == 1 else "i confermati"}.')
    elif azione == 'abbina':
        if not (item_id and tx_id):
            messages.error(request, 'Scegli il lavaggio delle spazzole.')
            return
        ok, msg = abbinamento_portali.abbina_manuale(chiusura, item_id, tx_id, request.user)
        (messages.success if ok else messages.error)(request, msg)
    elif azione == 'rifiuta':
        ok = bool(item_id and tx_id) and abbinamento_portali.rifiuta(chiusura, item_id, tx_id, request.user)
        (messages.success if ok else messages.error)(request, 'Proposta scartata.' if ok else 'Proposta non trovata.')
    elif azione == 'rimuovi':
        ok = abbinamento_portali.rimuovi(chiusura, _intero(p.get('abbinamento_id')), request.user)
        (messages.success if ok else messages.error)(request, 'Abbinamento tolto.' if ok else 'Abbinamento non trovato.')
    elif azione == 'senza_portale':
        ok = bool(item_id) and abbinamento_portali.chiudi_senza_portale(chiusura, item_id, request.user)
        (messages.success if ok else messages.error)(
            request, 'Segnato come non passato dalle spazzole.' if ok else 'Lavaggio non trovato.')
    elif azione == 'riapri':
        ok = bool(item_id) and abbinamento_portali.riapri_senza_portale(chiusura, item_id)
        (messages.success if ok else messages.error)(request, 'Lavaggio riaperto.' if ok else 'Lavaggio non trovato.')
    else:
        messages.error(request, 'Azione non valida.')


@login_required
@require_POST
def aggiorna_washtec(request):
    """Tasto "Aggiorna ora": chiede al worker un import subito."""
    if not fa_spazzole(request.user):
        raise PermissionDenied
    import_automatico.richiedi(request.user)
    if request.headers.get('X-Requested-With') == 'fetch':
        return JsonResponse({'ok': True, **import_automatico.stato_json()})
    messages.info(request, 'Aggiornamento da WashTec avviato: ci vuole circa un minuto.')
    torna = request.POST.get('torna', '')
    if not url_has_allowed_host_and_scheme(torna, {request.get_host()}):
        torna = reverse('ordini:spazzole-giornata')
    return redirect(torna)


@login_required
def stato_washtec(request):
    if not fa_spazzole(request.user):
        raise PermissionDenied
    return JsonResponse(import_automatico.stato_json())
