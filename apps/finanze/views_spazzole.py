"""Spazzole <-> servito per gli operatori (riquadro della "Distinta", sezione Ordini).

Le transazioni WashTec arrivano da sole (import automatico, vedi
services/import_automatico.py; "Aggiorna ora" per non aspettare il giro
successivo). Il riquadro di abbinamento e' lo stesso del report giornata
(finanze/_abbinamento_portali.html, azioni in views.azione_abbinamento_portali).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.auth_system.sezioni import ha_accesso

from .services import import_automatico
from .views_chiusura import _data


def fa_spazzole(user):
    """Conferma gli abbinamenti spazzole <-> servito e avvia l'import."""
    return ha_accesso(user, 'spazzole_servito') or ha_accesso(user, 'finanze')


def _alla_distinta(giorno, proposte):
    return (f"{reverse('ordini:chiusura-giornata')}?data={giorno:%Y-%m-%d}"
            f"{'&proposte=1' if proposte else ''}#abbinamento-portali")


@login_required
def spazzole_giornata(request):
    """Vecchia pagina Spazzole: l'abbinamento ora e' nella Distinta."""
    if not fa_spazzole(request.user):
        raise PermissionDenied
    return redirect(_alla_distinta(_data(request), request.GET.get('proposte') == '1'))


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
        torna = reverse('ordini:chiusura-giornata') + '#abbinamento-portali'
    return redirect(torna)


@login_required
def stato_washtec(request):
    if not fa_spazzole(request.user):
        raise PermissionDenied
    return JsonResponse(import_automatico.stato_json())
