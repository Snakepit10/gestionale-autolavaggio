"""Viste del modulo Marketing/CRM.

F1: dashboard segmenti + dettaglio segmento + export CSV + impostazioni.
Le fasi successive aggiungono composer campagne, dashboard conversioni.
"""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import ImpostazioniMarketing
from .services.segmentazione import SEGMENTI_LABEL, segmenta_clienti


def _staff_required(view):
    """Il modulo marketing e' riservato allo staff."""
    from functools import wraps

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, 'Sezione riservata allo staff.')
            return redirect('core:home')
        return view(request, *args, **kwargs)
    return login_required(wrapper)


@_staff_required
def dashboard(request):
    """Pagina principale: 4 card segmento con conteggi."""
    ris = segmenta_clienti()
    segmenti = [
        (chiave, SEGMENTI_LABEL[chiave], len(ris.get(chiave)))
        for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
    ]
    cfg = ImpostazioniMarketing.get_solo()
    return render(request, 'marketing/dashboard.html', {
        'segmenti': segmenti,
        'cfg': cfg,
    })


@_staff_required
def segmento_dettaglio(request, chiave):
    """Lista clienti di un segmento."""
    if chiave not in SEGMENTI_LABEL:
        messages.error(request, 'Segmento sconosciuto.')
        return redirect('marketing:dashboard')
    ris = segmenta_clienti()
    return render(request, 'marketing/segmento.html', {
        'chiave': chiave,
        'label': SEGMENTI_LABEL[chiave],
        'clienti': ris.get(chiave),
    })


@_staff_required
def segmento_export_csv(request, chiave):
    """Export CSV del segmento (nome, telefono, ultimo lavaggio, ...)."""
    if chiave not in SEGMENTI_LABEL:
        return HttpResponse('Segmento sconosciuto', status=404)
    ris = segmenta_clienti()
    oggi = timezone.localtime(timezone.now()).strftime('%Y%m%d')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="segmento_{chiave}_{oggi}.csv"'
    )
    # BOM per Excel che altrimenti sbaglia l'encoding degli accenti
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Nome', 'Telefono', 'Email', 'Ultimo lavaggio',
        'Totale lavaggi', 'Frequenza media (gg)', 'Giorni da ultimo',
    ])
    for cs in ris.get(chiave):
        writer.writerow([
            cs.nome_completo,
            cs.telefono,
            cs.cliente.email or '',
            timezone.localtime(cs.ultimo_lavaggio).strftime('%d/%m/%Y'),
            cs.totale_lavaggi,
            f'{cs.frequenza_media_giorni:.0f}' if cs.frequenza_media_giorni else '',
            cs.giorni_da_ultimo,
        ])
    return response


@_staff_required
def toggle_opt_out(request, cliente_id):
    """Attiva/disattiva 'non contattare' su un cliente (POST).

    Usato dal bottone nelle liste segmento e richiamabile da altre
    pagine (es. inbox). Redirect alla pagina di provenienza.
    """
    from apps.clienti.models import Cliente
    from django.shortcuts import get_object_or_404

    if request.method != 'POST':
        return redirect('marketing:dashboard')
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    if cliente.blocca_marketing:
        cliente.rimuovi_opt_out()
        messages.success(request, f'{cliente}: opt-out rimosso, di nuovo contattabile.')
    else:
        cliente.imposta_opt_out(motivo=f'manuale da {request.user.username}')
        messages.success(request, f'{cliente}: segnato come "non contattare".')
    return redirect(request.POST.get('next') or 'marketing:dashboard')


@_staff_required
def impostazioni(request):
    """Form impostazioni marketing (singleton)."""
    cfg = ImpostazioniMarketing.get_solo()

    if request.method == 'POST':
        campi_int = [
            'giorni_dormiente', 'giorni_rallentamento_delta',
            'max_invii_giorno', 'intervallo_min_secondi',
            'intervallo_max_secondi', 'finestra_no_ricontatto_giorni',
            'richiamo_giorni_dopo', 'giorni_finestra_conversione',
        ]
        try:
            for campo in campi_int:
                val = int(request.POST.get(campo, getattr(cfg, campo)))
                if val < 0:
                    raise ValueError(campo)
                setattr(cfg, campo, val)
        except (TypeError, ValueError):
            messages.error(request, 'Valori non validi: usa numeri interi >= 0.')
            return redirect('marketing:impostazioni')

        if cfg.intervallo_min_secondi > cfg.intervallo_max_secondi:
            messages.error(request, "L'intervallo minimo non puo' superare il massimo.")
            return redirect('marketing:impostazioni')

        cfg.richiamo_automatico_attivo = request.POST.get('richiamo_automatico_attivo') == 'on'
        cfg.richiamo_template_meta = (request.POST.get('richiamo_template_meta') or '').strip()
        cfg.save()
        messages.success(request, 'Impostazioni salvate.')
        return redirect('marketing:impostazioni')

    return render(request, 'marketing/impostazioni.html', {'cfg': cfg})
