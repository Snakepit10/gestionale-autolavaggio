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
def campagne_list(request):
    """Elenco campagne con statistiche complete + riepilogo per segmento."""
    from .models import Campagna
    from .services.statistiche import statistiche_campagna, statistiche_per_segmento

    campagne = [
        (c, statistiche_campagna(c)) for c in Campagna.objects.all()
    ]
    return render(request, 'marketing/campagne_list.html', {
        'campagne': campagne,
        'per_segmento': statistiche_per_segmento(),
        'segmenti_label': SEGMENTI_LABEL,
    })


@_staff_required
def campagna_nuova(request):
    """Step 1 del composer: nome, segmenti (multi), template Meta, parametri."""
    from apps.clienti.models import Cliente
    from .services.campagne import PLACEHOLDER_SUPPORTATI
    ris = segmenta_clienti()
    segmenti = [
        (chiave, SEGMENTI_LABEL[chiave], len(ris.get(chiave)))
        for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
    ]
    return render(request, 'marketing/campagna_nuova.html', {
        'segmenti': segmenti,
        'n_tutti': Cliente.objects.exclude(telefono='').count(),
        'placeholder': PLACEHOLDER_SUPPORTATI,
    })


def _risolvi_destinatari_da_segmenti(segmenti_scelti: list[str]) -> list[int]:
    """Unione (dedup) dei cliente_id dei segmenti scelti.

    'tutti' = intera anagrafica con telefono (per comunicazioni di
    servizio tipo "nuovo sistema di prenotazione"): ingloba qualsiasi
    altro segmento selezionato. I filtri di eleggibilita' (opt-out,
    no-ricontatto, telefono valido) vengono comunque applicati dopo.
    """
    from apps.clienti.models import Cliente

    if 'tutti' in segmenti_scelti:
        return list(
            Cliente.objects.exclude(telefono='').values_list('pk', flat=True)
        )
    ris = segmenta_clienti()
    ids = []
    visti = set()
    for seg in segmenti_scelti:
        for cs in ris.get(seg):
            if cs.cliente.pk not in visti:
                visti.add(cs.cliente.pk)
                ids.append(cs.cliente.pk)
    return ids


@_staff_required
def campagna_preview(request):
    """Step 2: anteprima destinatari + messaggi compilati, prima della conferma."""
    from .services.campagne import prepara_destinatari, risolvi_params

    if request.method != 'POST':
        return redirect('marketing:campagna-nuova')

    nome = (request.POST.get('nome') or '').strip()
    segmenti_scelti = [
        s for s in request.POST.getlist('segmenti')
        if s in SEGMENTI_LABEL or s == 'tutti'
    ]
    template_meta = (request.POST.get('template_meta') or '').strip()
    # Un parametro per riga nella textarea
    params_raw = request.POST.get('template_params') or ''
    template_params = [r.strip() for r in params_raw.splitlines() if r.strip()]

    if not nome or not template_meta:
        messages.error(request, 'Nome campagna e template Meta sono obbligatori.')
        return redirect('marketing:campagna-nuova')
    if not segmenti_scelti:
        messages.error(request, 'Seleziona almeno un segmento di destinatari.')
        return redirect('marketing:campagna-nuova')

    cliente_ids = _risolvi_destinatari_da_segmenti(segmenti_scelti)
    eleggibili, esclusi = prepara_destinatari(cliente_ids)

    # Esempi di messaggi compilati sui primi 3 eleggibili
    esempi = [
        (c, risolvi_params(template_params, c))
        for c in eleggibili[:3]
    ]

    if 'tutti' in segmenti_scelti:
        segmento_label = 'Tutti i clienti'
    else:
        segmento_label = ' + '.join(SEGMENTI_LABEL[s] for s in segmenti_scelti)

    return render(request, 'marketing/campagna_preview.html', {
        'nome': nome,
        'segmenti_scelti': segmenti_scelti,
        'segmento_label': segmento_label,
        'template_meta': template_meta,
        'template_params': template_params,
        'params_raw': params_raw,
        'eleggibili': eleggibili,
        'esclusi': esclusi,
        'esempi': esempi,
    })


@_staff_required
def campagna_crea(request):
    """Step 3: conferma finale -> crea campagna + invii in coda."""
    from .services.campagne import crea_campagna

    if request.method != 'POST':
        return redirect('marketing:campagna-nuova')

    nome = (request.POST.get('nome') or '').strip()
    segmenti_scelti = [
        s for s in request.POST.getlist('segmenti')
        if s in SEGMENTI_LABEL or s == 'tutti'
    ]
    template_meta = (request.POST.get('template_meta') or '').strip()
    params_raw = request.POST.get('template_params') or ''
    template_params = [r.strip() for r in params_raw.splitlines() if r.strip()]
    # Id selezionati dalle checkbox della preview
    try:
        cliente_ids = [int(x) for x in request.POST.getlist('destinatari')]
    except ValueError:
        messages.error(request, 'Destinatari non validi.')
        return redirect('marketing:campagna-nuova')

    if not cliente_ids:
        messages.error(request, 'Seleziona almeno un destinatario.')
        return redirect('marketing:campagna-nuova')

    campagna = crea_campagna(
        nome=nome, template_meta=template_meta,
        template_params=template_params,
        cliente_ids_selezionati=cliente_ids,
        segmento=','.join(segmenti_scelti), user=request.user,
    )
    messages.success(
        request,
        f'Campagna "{campagna.nome}" creata: {campagna.n_in_coda} invii in coda. '
        f"L'invio parte scaglionato (max {ImpostazioniMarketing.get_solo().max_invii_giorno}/giorno)."
    )
    return redirect('marketing:campagna-dettaglio', pk=campagna.pk)


@_staff_required
def campagna_dettaglio(request, pk):
    """Dettaglio campagna: stati invii + conversioni (F6)."""
    from django.shortcuts import get_object_or_404
    from .models import Campagna
    from .services.statistiche import statistiche_campagna

    campagna = get_object_or_404(Campagna, pk=pk)
    invii = campagna.invii.select_related('cliente', 'messaggio_wa').order_by('stato', '-inviato_il')
    stats = statistiche_campagna(campagna)

    return render(request, 'marketing/campagna_dettaglio.html', {
        'campagna': campagna,
        'invii': invii,
        'n_conversioni': stats['n_conversioni'],
        'tasso_conversione': stats['tasso_conversione'],
        'fatturato': stats['fatturato'],
    })


@_staff_required
def processa_coda_ora(request):
    """POST: avvia subito il processamento della coda invii in un
    thread in background (senza aspettare il cron).

    Utile per il primo invio o per smaltire la coda a mano. Il batch
    rispetta comunque tetto giornaliero e pause casuali tra i
    messaggi, quindi la coda si svuota progressivamente: ricaricare la
    pagina per vedere gli stati aggiornarsi.
    """
    if request.method != 'POST':
        return redirect('marketing:campagne')

    from .models import InvioCampagna
    from .services.invio import avvia_processamento_background

    n_in_coda = InvioCampagna.objects.filter(
        stato='in_coda', campagna__stato__in=['in_coda', 'in_corso']).count()
    cfg_fascia = ImpostazioniMarketing.get_solo()
    if n_in_coda == 0:
        messages.info(request, 'Nessun invio in coda.')
    elif not cfg_fascia.in_fascia_invio():
        messages.warning(
            request,
            f'Fuori fascia di invio ({cfg_fascia.orario_invio_da:%H:%M}-'
            f'{cfg_fascia.orario_invio_a:%H:%M}): la coda non parte. '
            f'Modifica la fascia nelle impostazioni oppure usa "Invia ora" '
            f'sul singolo cliente.'
        )
    else:
        avvia_processamento_background(max_batch=8)
        cfg = ImpostazioniMarketing.get_solo()
        messages.success(
            request,
            f'Invio avviato in background ({min(n_in_coda, 8)} messaggi in '
            f'questo batch, pause di {cfg.intervallo_min_secondi}-'
            f'{cfg.intervallo_max_secondi}s tra un messaggio e l\'altro). '
            f'Ricarica la pagina tra qualche minuto per vedere gli stati.'
        )
    return redirect(request.POST.get('next') or 'marketing:campagne')


@_staff_required
def invio_singolo(request, pk, invio_id):
    """POST: invia SUBITO il messaggio della campagna a un singolo
    destinatario, senza aspettare coda/cron. Funziona anche come
    'riprova' per invii falliti o saltati.
    """
    from django.shortcuts import get_object_or_404
    from .models import InvioCampagna
    from .services.invio import invia_singolo as _invia_singolo

    if request.method != 'POST':
        return redirect('marketing:campagna-dettaglio', pk=pk)

    invio = get_object_or_404(
        InvioCampagna.objects.select_related('campagna', 'cliente'),
        pk=invio_id, campagna_id=pk,
    )
    ok, msg = _invia_singolo(invio)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('marketing:campagna-dettaglio', pk=pk)


@_staff_required
def campagna_pausa(request, pk):
    """Mette in pausa / riprende una campagna.

    In pausa: il cron e il bottone 'Processa coda ora' non toccano i
    suoi invii (la coda seleziona solo campagne in_coda/in_corso).
    Gli invii restano 'in_coda' e ripartono al 'Riprendi'. Diverso da
    'Annulla' che e' definitivo.
    """
    from django.shortcuts import get_object_or_404
    from .models import Campagna

    if request.method != 'POST':
        return redirect('marketing:campagne')
    campagna = get_object_or_404(Campagna, pk=pk)

    if campagna.stato in ('in_coda', 'in_corso'):
        campagna.stato = 'in_pausa'
        campagna.save(update_fields=['stato'])
        messages.success(
            request,
            f'Campagna "{campagna.nome}" in pausa: gli invii rimanenti sono '
            f'sospesi finche\' non la riprendi.'
        )
    elif campagna.stato == 'in_pausa':
        # Riprendi: in_corso se ha gia' inviato qualcosa, altrimenti in_coda
        nuovo = 'in_corso' if campagna.invii.filter(stato='inviato').exists() else 'in_coda'
        campagna.stato = nuovo
        campagna.save(update_fields=['stato'])
        messages.success(
            request,
            f'Campagna "{campagna.nome}" ripresa: gli invii in coda '
            f'ripartiranno dal prossimo batch.'
        )
    else:
        messages.error(request, 'La campagna non e\' in uno stato pausabile.')
    return redirect('marketing:campagna-dettaglio', pk=pk)


@_staff_required
def campagna_annulla(request, pk):
    """Annulla una campagna: gli invii ancora in coda non partiranno."""
    from django.shortcuts import get_object_or_404
    from .models import Campagna

    if request.method != 'POST':
        return redirect('marketing:campagne')
    campagna = get_object_or_404(Campagna, pk=pk)
    if campagna.stato in ('in_coda', 'in_corso', 'in_pausa'):
        campagna.invii.filter(stato='in_coda').update(
            stato='saltato', motivo_salto='campagna annullata')
        campagna.stato = 'annullata'
        campagna.save(update_fields=['stato'])
        messages.success(request, f'Campagna "{campagna.nome}" annullata.')
    else:
        messages.error(request, 'La campagna non e\' annullabile in questo stato.')
    return redirect('marketing:campagna-dettaglio', pk=pk)


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

        # Fascia oraria di invio (HH:MM). Ammessa anche a cavallo di
        # mezzanotte (es. 21:00-02:00): in_fascia_invio la gestisce.
        from datetime import datetime as _dt
        try:
            for campo in ('orario_invio_da', 'orario_invio_a'):
                raw = (request.POST.get(campo) or '').strip()
                if raw:
                    setattr(cfg, campo, _dt.strptime(raw, '%H:%M').time())
        except (TypeError, ValueError):
            messages.error(request, 'Fascia oraria non valida: usa il formato HH:MM.')
            return redirect('marketing:impostazioni')

        cfg.richiamo_automatico_attivo = request.POST.get('richiamo_automatico_attivo') == 'on'
        cfg.richiamo_template_meta = (request.POST.get('richiamo_template_meta') or '').strip()
        cfg.save()
        messages.success(request, 'Impostazioni salvate.')
        return redirect('marketing:impostazioni')

    return render(request, 'marketing/impostazioni.html', {'cfg': cfg})
