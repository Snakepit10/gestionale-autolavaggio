"""Viste STAFF del Garage (prefisso /garage/, in STAFF_PREFIXES).

Registrazione dei servizi professionali sul libretto: l'operatore
cerca il veicolo per targa e registra il servizio effettuato. Stesso
pattern della pagina "Avvia lavaggio" staff delle monete (ricerca
AJAX + PRG con ?veicolo=<pk> in querystring).
Accesso: gruppi operativi (gli operatori possono non avere is_staff).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import EventoLibretto, TipoServizioGarage, Veicolo, normalizza_targa
from .services.motore import registra_evento

GRUPPI_OPERATIVI = ('operatore', 'responsabile', 'titolare')


def _operatore_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not (u.is_staff or u.is_superuser
                or u.groups.filter(name__in=GRUPPI_OPERATIVI).exists()):
            messages.error(request, 'Sezione riservata agli operatori.')
            return redirect('core:home')
        return view(request, *args, **kwargs)
    return login_required(wrapper)


@_operatore_required
def registra(request):
    """Pagina staff: cerca veicolo per targa e registra un servizio."""
    veicolo_pre = None
    pre_pk = request.GET.get('veicolo') or request.POST.get('veicolo_id')
    if pre_pk and str(pre_pk).isdigit():
        veicolo_pre = (Veicolo.objects.filter(pk=int(pre_pk), attivo=True)
                       .select_related('cliente').first())

    if request.method == 'POST':
        tipo = TipoServizioGarage.objects.filter(
            pk=request.POST.get('tipo_id'), attivo=True).first()
        if veicolo_pre is None or tipo is None:
            messages.error(request, 'Seleziona un veicolo e un servizio validi.')
            return redirect(request.path)

        # Data opzionale (retrodatazione, es. servizio di ieri)
        data = None
        raw = (request.POST.get('data') or '').strip()
        if raw:
            from datetime import datetime as _dt
            try:
                data = timezone.make_aware(_dt.strptime(raw, '%Y-%m-%dT%H:%M'))
            except (TypeError, ValueError):
                messages.error(request, 'Data non valida.')
                return redirect(f'{request.path}?veicolo={veicolo_pre.pk}')
            if data > timezone.now():
                messages.error(request, 'La data non puo\' essere nel futuro.')
                return redirect(f'{request.path}?veicolo={veicolo_pre.pk}')

        esito = registra_evento(
            veicolo_pre, tipo, 'operatore',
            data=data, note=(request.POST.get('note') or '').strip(),
            registrato_da=request.user,
        )
        messages.success(
            request,
            f'{tipo.nome} registrato su {veicolo_pre.targa}: salute '
            f'{esito.salute_prima} -> {esito.salute_dopo}.')
        return redirect(f'{request.path}?veicolo={veicolo_pre.pk}')

    ultimi = []
    if veicolo_pre is not None:
        ultimi = (veicolo_pre.eventi
                  .select_related('tipo_servizio', 'registrato_da')[:8])
    return render(request, 'garage/registra.html', {
        'veicolo_pre': veicolo_pre,
        'salute_pre': veicolo_pre.salute_attuale if veicolo_pre else None,
        'tipi': TipoServizioGarage.objects.filter(attivo=True)
                .exclude(slug='self_service'),
        'ultimi': ultimi,
    })


@_operatore_required
def api_cerca_targa(request):
    """Ricerca veicoli per targa (o nome cliente) per la pagina staff."""
    term = (request.GET.get('term') or '').strip()
    if len(term) < 2:
        return JsonResponse({'results': []})
    from django.db.models import Q
    qs = (Veicolo.objects.filter(attivo=True)
          .filter(Q(targa__icontains=normalizza_targa(term))
                  | Q(cliente__nome__icontains=term)
                  | Q(cliente__cognome__icontains=term)
                  | Q(cliente__ragione_sociale__icontains=term))
          .select_related('cliente')[:20])
    return JsonResponse({'results': [
        {'id': v.pk, 'targa': v.targa,
         'veicolo': f'{v.marca} {v.modello}',
         'cliente': v.cliente.nome_completo or str(v.cliente)}
        for v in qs
    ]})
