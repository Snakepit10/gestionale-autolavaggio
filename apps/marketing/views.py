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


def _chiavi_segmenti_valide(post_list):
    """Filtra le chiavi segmento arrivate dal form: automatici, 'tutti'
    e personalizzati esistenti ('custom:<pk>')."""
    from .models import SegmentoPersonalizzato
    valide = []
    for s in post_list:
        if s in SEGMENTI_LABEL or s == 'tutti':
            valide.append(s)
        elif s.startswith('custom:') and s[7:].isdigit() and \
                SegmentoPersonalizzato.objects.filter(pk=int(s[7:])).exists():
            valide.append(s)
    return valide


def _label_chiavi(chiavi):
    """Label leggibile di una lista di chiavi segmento (anche custom)."""
    from .models import SegmentoPersonalizzato
    if 'tutti' in chiavi:
        return 'Tutti i clienti'
    labels = []
    for s in chiavi:
        if s in SEGMENTI_LABEL:
            labels.append(SEGMENTI_LABEL[s])
        elif s.startswith('custom:'):
            seg = SegmentoPersonalizzato.objects.filter(pk=int(s[7:])).first()
            labels.append(seg.nome if seg else 'Segmento eliminato')
    return ' + '.join(labels)


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
    """Pagina principale: KPI, card segmento, grafici e consigli.

    Le statistiche per-cliente si calcolano UNA volta e si riusano per
    segmentazione e consigli. analisi_ai() e' il seam (oggi inerte)
    per i futuri consigli generati dall'AI.
    """
    from .models import SegmentoPersonalizzato
    from .services.consigli import analisi_ai, genera_consigli
    from .services.segmentazione import (filtra_segmento_personalizzato,
                                         statistiche_clienti)
    from .services.statistiche import (kpi_dashboard, serie_mensile,
                                       stats_ultime_campagne)

    stats = statistiche_clienti()
    ris = segmenta_clienti(stats=stats)
    cfg = ImpostazioniMarketing.get_solo()

    # Come viene filtrato ogni segmento automatico (mostrato in card,
    # con le soglie correnti delle impostazioni).
    descrizioni_auto = {
        'attivi': 'Ultimo lavaggio in linea con la frequenza abituale del '
                  'cliente (entro la sua media personale + '
                  f'{cfg.giorni_rallentamento_delta} giorni).',
        'rallentamento': 'Fermi da piu\' della loro frequenza media '
                         f'personale + {cfg.giorni_rallentamento_delta} '
                         'giorni, ma non ancora dormienti.',
        'dormienti': f'Nessun lavaggio da oltre {cfg.giorni_dormiente} '
                     'giorni.',
        'one_shot': 'Un solo lavaggio in totale, mai tornati (anche se '
                    'vecchissimo: la comunicazione per loro e\' diversa).',
    }
    # Finestra di confronto dei delta KPI: selezionabile 7/30/90 giorni
    # (default 30) per distinguere oscillazioni brevi da cambi
    # strutturali.
    try:
        confronto = int(request.GET.get('confronto', 30))
    except (TypeError, ValueError):
        confronto = 30
    if confronto not in (7, 30, 90):
        confronto = 30

    # Trend KPI dei segmenti (serie storiche ricostruite, cache 1h):
    # i delta finiscono anche sulle card come indicatore di andamento.
    from .services.statistiche import statistiche_per_segmento
    from .services.trend import serie_trend_segmenti
    trend = serie_trend_segmenti(giorni_confronto=confronto)

    # Conversione storica delle campagne per segmento (match esatto
    # sulla chiave: campagne multi-segmento restano fuori).
    conversioni_seg = {
        r['segmento']: r for r in statistiche_per_segmento()
        if r['n_inviati'] >= 5
    }
    totale_clienti = len(stats) or 1

    def _card(chiave, label, membri, descrizione, seg_obj=None,
              criteri=None):
        conv = conversioni_seg.get(chiave)
        return {
            'chiave': chiave, 'label': label,
            'conteggio': len(membri),
            'pct': round(len(membri) / totale_clienti * 100, 1),
            'descrizione': descrizione,
            'criteri': criteri,
            'delta': trend['delta'].get(chiave),
            'spesa_storica': sum(float(cs.totale_speso) for cs in membri),
            'conv_campagne': round(conv['tasso_conversione'], 1) if conv else None,
            'seg': seg_obj,
        }

    membri_per_chiave = {
        chiave: ris.get(chiave)
        for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
    }
    segmenti = [
        _card(chiave, SEGMENTI_LABEL[chiave], membri_per_chiave[chiave],
              descrizioni_auto[chiave])
        for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
    ]
    segmenti_custom = []
    for s in SegmentoPersonalizzato.objects.filter(attivo=True):
        membri = filtra_segmento_personalizzato(s, stats)
        membri_per_chiave[s.chiave] = membri
        segmenti_custom.append(
            _card(s.chiave, s.nome, membri, s.descrizione, seg_obj=s,
                  criteri=s.criteri_leggibili()))

    # Incrocio segmenti dalla UI: ?incrocio_a=<chiave>&incrocio_b=<chiave>
    opzioni_incrocio = (
        [(c['chiave'], c['label']) for c in segmenti]
        + [(c['chiave'], c['label']) for c in segmenti_custom]
    )
    incrocio = None
    inc_a = request.GET.get('incrocio_a', '')
    inc_b = request.GET.get('incrocio_b', '')
    chiavi_valide = dict(opzioni_incrocio)
    if inc_a in chiavi_valide and inc_b in chiavi_valide and inc_a != inc_b:
        mappa_a = {cs.cliente.pk: cs for cs in membri_per_chiave[inc_a]}
        comuni = [mappa_a[cs.cliente.pk] for cs in membri_per_chiave[inc_b]
                  if cs.cliente.pk in mappa_a]
        comuni.sort(key=lambda cs: -cs.totale_speso)
        incrocio = {
            'a': chiavi_valide[inc_a], 'b': chiavi_valide[inc_b],
            'chiave_a': inc_a, 'chiave_b': inc_b,
            'n': len(comuni),
            'spesa': sum(float(cs.totale_speso) for cs in comuni),
            'clienti': comuni[:50],
        }

    kpi = kpi_dashboard()
    serie = serie_mensile(6)
    ultime = stats_ultime_campagne(8)

    # Tabella dati del trend (collassabile sotto al grafico): una riga
    # per settimana, una colonna per serie.
    trend_tabella = {
        'intestazioni': [s['nome'] for s in trend['serie']],
        'righe': [
            [trend['labels'][i]] + [s['valori'][i] for s in trend['serie']]
            for i in range(len(trend['labels']))
        ],
    }
    consigli = genera_consigli(stats, ris, cfg) + analisi_ai()
    consigli.sort(key=lambda c: -c['priorita'])

    chart_data = {
        'serie': serie,
        'trend_segmenti': {
            'labels': trend['labels'],
            'serie': [{'nome': s['nome'], 'colore': s['colore'],
                       'tipo': s['tipo'], 'valori': s['valori']}
                      for s in trend['serie']],
        },
        'segmenti': {
            'labels': [c['label'] for c in segmenti],
            'valori': [c['conteggio'] for c in segmenti],
        },
        'campagne': {
            'labels': [c['nome'] for c in ultime],
            'lettura': [c['tasso_lettura'] for c in ultime],
            'conversione': [c['tasso_conversione'] for c in ultime],
        },
    }

    return render(request, 'marketing/dashboard.html', {
        'segmenti': segmenti,
        'segmenti_custom': segmenti_custom,
        'totale_clienti': totale_clienti,
        'confronto': confronto,
        'trend_generato_il': trend.get('generato_il', ''),
        'trend_tabella': trend_tabella,
        'opzioni_incrocio': opzioni_incrocio,
        'incrocio': incrocio,
        'inc_a': inc_a, 'inc_b': inc_b,
        'cfg': cfg,
        'kpi': kpi,
        'consigli': consigli,
        'chart_data': chart_data,
        'ha_serie': any(serie['invii']) or any(serie['conversioni']),
        'ha_campagne': bool(ultime),
    })


@_staff_required
def trend_export_csv(request):
    """Export CSV dell'intero andamento settimanale dei segmenti
    (stessi dati del grafico trend: una riga per settimana, una
    colonna per segmento)."""
    from .services.trend import serie_trend_segmenti

    trend = serie_trend_segmenti()
    oggi = timezone.localtime(timezone.now()).strftime('%Y%m%d')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="trend_segmenti_{oggi}.csv"'
    )
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Settimana'] + [s['nome'] for s in trend['serie']])
    for i, label in enumerate(trend['labels']):
        writer.writerow([label] + [s['valori'][i] for s in trend['serie']])
    return response


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
    """Step 1 del composer: nome, segmenti (multi), template Meta, parametri.

    Supporta il prefill via querystring:
    - ?duplica=<pk>   copia nome/template/parametri/segmenti da una
      campagna esistente (anche automatica: la copia nasce manuale);
    - ?segmenti=a,b   preseleziona i segmenti (usato dai CTA dei
      consigli in dashboard).
    """
    from apps.clienti.models import Cliente
    from .models import Campagna, SegmentoPersonalizzato
    from .services.campagne import PLACEHOLDER_SUPPORTATI
    from .services.segmentazione import (filtra_segmento_personalizzato,
                                         statistiche_clienti)
    stats = statistiche_clienti()
    ris = segmenta_clienti(stats=stats)
    segmenti = [
        (chiave, SEGMENTI_LABEL[chiave], len(ris.get(chiave)))
        for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
    ]
    segmenti_custom = [
        (s.chiave, s.nome, len(filtra_segmento_personalizzato(s, stats)))
        for s in SegmentoPersonalizzato.objects.filter(attivo=True)
    ]

    prefill = {'nome': '', 'template_meta': '', 'params_raw': '', 'segmenti': [],
               'flusso_continuo': False, 'riaggancio_giorni': 0}
    duplica_pk = request.GET.get('duplica', '')
    if duplica_pk.isdigit():
        orig = Campagna.objects.filter(pk=int(duplica_pk)).first()
        if orig is not None:
            prefill['nome'] = f'{orig.nome} (copia)'
            prefill['template_meta'] = orig.template_meta
            prefill['params_raw'] = '\n'.join(orig.template_params or [])
            prefill['flusso_continuo'] = orig.flusso_continuo
            prefill['riaggancio_giorni'] = orig.riaggancio_giorni
            chiavi = [s for s in orig.segmento_origine.split(',') if s]
            prefill['segmenti'] = _chiavi_segmenti_valide(chiavi)
            # Segmenti custom eliminati nel frattempo, o chiavi non piu'
            # selezionabili (es. 'richiamo_automatico'): avvisa che la
            # preselezione e' parziale.
            if len(prefill['segmenti']) < len(chiavi):
                messages.info(
                    request,
                    'Alcuni segmenti della campagna originale non esistono '
                    'piu\': controlla la selezione dei destinatari.')
    elif request.GET.get('segmenti'):
        prefill['segmenti'] = _chiavi_segmenti_valide(
            request.GET['segmenti'].split(','))

    # Prefill esplicito (usato dalle proposte del consulente AI):
    # sovrascrive i singoli campi se presenti in querystring.
    if request.GET.get('nome'):
        prefill['nome'] = request.GET['nome']
    if request.GET.get('template'):
        prefill['template_meta'] = request.GET['template']
    if request.GET.getlist('param'):
        prefill['params_raw'] = '\n'.join(request.GET.getlist('param'))

    return render(request, 'marketing/campagna_nuova.html', {
        'segmenti': segmenti,
        'segmenti_custom': segmenti_custom,
        'n_tutti': Cliente.objects.exclude(telefono='').count(),
        'placeholder': PLACEHOLDER_SUPPORTATI,
        'prefill': prefill,
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
    from .models import SegmentoPersonalizzato
    from .services.segmentazione import (filtra_segmento_personalizzato,
                                         statistiche_clienti)

    chiavi_auto = [s for s in segmenti_scelti if s in SEGMENTI_LABEL]
    chiavi_custom = [s for s in segmenti_scelti if s.startswith('custom:')]

    stats = statistiche_clienti()
    ris = segmenta_clienti(stats=stats) if chiavi_auto else None

    ids = []
    visti = set()

    def aggiungi(lista):
        for cs in lista:
            if cs.cliente.pk not in visti:
                visti.add(cs.cliente.pk)
                ids.append(cs.cliente.pk)

    for seg in chiavi_auto:
        aggiungi(ris.get(seg))
    for chiave in chiavi_custom:
        seg = SegmentoPersonalizzato.objects.filter(pk=int(chiave[7:])).first()
        if seg is not None:
            aggiungi(filtra_segmento_personalizzato(seg, stats))
    return ids


@_staff_required
def campagna_preview(request):
    """Step 2: anteprima destinatari + messaggi compilati, prima della conferma."""
    from .services.campagne import prepara_destinatari, risolvi_params

    if request.method != 'POST':
        return redirect('marketing:campagna-nuova')

    nome = (request.POST.get('nome') or '').strip()
    segmenti_scelti = _chiavi_segmenti_valide(request.POST.getlist('segmenti'))
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

    segmento_label = _label_chiavi(segmenti_scelti)

    flusso_continuo = request.POST.get('flusso_continuo') == 'on'
    try:
        riaggancio_giorni = max(0, int(request.POST.get('riaggancio_giorni') or 0))
    except (TypeError, ValueError):
        riaggancio_giorni = 0

    # Confronta i parametri con le variabili REALI del template su Meta:
    # un mismatch produce l'errore 132000 a ogni invio. Se il body non
    # e' recuperabile (template nuovo, rete) non si avvisa.
    from apps.clients.whatsapp import _conta_variabili_template
    n_variabili = _conta_variabili_template(template_meta)
    avviso_parametri = None
    if n_variabili is not None and n_variabili != len(template_params):
        if n_variabili == 0:
            avviso_parametri = (
                f'Il template "{template_meta}" non ha variabili: i '
                f'{len(template_params)} parametri inseriti verranno ignorati.')
        else:
            avviso_parametri = (
                f'Il template "{template_meta}" ha {n_variabili} '
                f'variabile/i ma hai inserito {len(template_params)} '
                f'parametro/i: gli invii FALLIRANNO (errore Meta 132000). '
                f'Torna indietro e inserisci una riga per ogni variabile '
                f'(es. {{nome}} per il nome del cliente).')

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
        'flusso_continuo': flusso_continuo,
        'riaggancio_giorni': riaggancio_giorni,
        'avviso_parametri': avviso_parametri,
    })


@_staff_required
def campagna_crea(request):
    """Step 3: conferma finale -> crea campagna + invii in coda."""
    from .services.campagne import crea_campagna

    if request.method != 'POST':
        return redirect('marketing:campagna-nuova')

    nome = (request.POST.get('nome') or '').strip()
    segmenti_scelti = _chiavi_segmenti_valide(request.POST.getlist('segmenti'))
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

    flusso_continuo = request.POST.get('flusso_continuo') == 'on'
    try:
        riaggancio_giorni = max(0, int(request.POST.get('riaggancio_giorni') or 0))
    except (TypeError, ValueError):
        riaggancio_giorni = 0

    campagna = crea_campagna(
        nome=nome, template_meta=template_meta,
        template_params=template_params,
        cliente_ids_selezionati=cliente_ids,
        segmento=','.join(segmenti_scelti), user=request.user,
        flusso_continuo=flusso_continuo,
        riaggancio_giorni=riaggancio_giorni,
    )
    msg = (f'Campagna "{campagna.nome}" creata: {campagna.n_in_coda} invii in coda. '
           f"L'invio parte scaglionato (max {ImpostazioniMarketing.get_solo().max_invii_giorno}/giorno).")
    if campagna.flusso_continuo:
        msg += (' Flusso continuo attivo: i nuovi clienti che entreranno nei '
                'segmenti verranno accodati automaticamente dal cron.')
    messages.success(request, msg)
    return redirect('marketing:campagna-dettaglio', pk=campagna.pk)


@_staff_required
def campagna_dettaglio(request, pk):
    """Dettaglio campagna: stati invii + conversioni (F6)."""
    from django.shortcuts import get_object_or_404
    from .models import Campagna
    from .services.statistiche import clienti_convertiti, statistiche_campagna

    campagna = get_object_or_404(Campagna, pk=pk)
    invii = campagna.invii.select_related('cliente', 'messaggio_wa').order_by('stato', '-inviato_il')
    stats = statistiche_campagna(campagna)

    return render(request, 'marketing/campagna_dettaglio.html', {
        'campagna': campagna,
        'invii': invii,
        'n_letti': stats['n_letti'],
        'tasso_lettura': stats['tasso_lettura'],
        'n_conversioni': stats['n_conversioni'],
        'tasso_conversione': stats['tasso_conversione'],
        'fatturato': stats['fatturato'],
        'convertiti': clienti_convertiti(campagna),
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
def campagna_elimina(request, pk):
    """Elimina definitivamente una campagna e i suoi invii (CASCADE).

    Ammessa solo su stati "fermi" (bozza, completata, annullata): una
    campagna con invii in movimento va prima annullata. L'inbox
    WhatsApp resta intatta (MessaggioWhatsApp non e' in cascade), ma:
    - le statistiche aggregate (rendimento per segmento, dashboard)
      perdono i numeri di questa campagna;
    - i clienti contattati tornano subito ricontattabili, perche' la
      finestra anti-ricontatto conta gli InvioCampagna 'inviato'.
    Il confirm nel template avvisa di entrambe le cose.
    """
    from django.shortcuts import get_object_or_404
    from .models import Campagna

    if request.method != 'POST':
        return redirect('marketing:campagne')
    campagna = get_object_or_404(Campagna, pk=pk)
    if campagna.stato not in ('bozza', 'completata', 'annullata'):
        messages.error(
            request,
            'La campagna e\' ancora attiva: prima annullala (o attendi il '
            'completamento), poi potrai eliminarla.')
        return redirect('marketing:campagna-dettaglio', pk=pk)
    nome = campagna.nome
    campagna.delete()
    messages.success(
        request,
        f'Campagna "{nome}" eliminata con tutti i suoi invii. I clienti '
        f'contattati non contano piu\' per la finestra anti-ricontatto.')
    return redirect('marketing:campagne')


@_staff_required
def campagna_riprova_falliti(request, pk):
    """Rimette in coda in blocco tutti gli invii falliti della campagna.

    Il reset di inviato_il e' obbligatorio: il claim atomico di
    services/invio.py pretende inviato_il IS NULL, e le righe fallite
    conservano il timestamp del tentativo. I 'saltato' NON vengono
    ritentati in blocco (hanno un motivo: opt-out, no telefono, ...):
    per quelli resta il bottone per-riga nel dettaglio.
    """
    from django.shortcuts import get_object_or_404
    from .models import Campagna

    if request.method != 'POST':
        return redirect('marketing:campagne')
    campagna = get_object_or_404(Campagna, pk=pk)
    if campagna.stato == 'annullata':
        messages.error(request, 'Campagna annullata: gli invii non si riprovano.')
        return redirect('marketing:campagna-dettaglio', pk=pk)

    n = campagna.invii.filter(stato='fallito').update(
        stato='in_coda', inviato_il=None, motivo_salto='')
    if n == 0:
        messages.info(request, 'Nessun invio fallito da riprovare.')
    else:
        # Una completata torna in_corso cosi' la coda la riprende; il
        # loop di chiusura del cron la ri-completera' da solo.
        if campagna.stato == 'completata':
            campagna.stato = 'in_corso'
            campagna.completata_il = None
            campagna.save(update_fields=['stato', 'completata_il'])
        messages.success(
            request,
            f'{n} invio/i fallito/i rimesso/i in coda: partiranno dal '
            f'prossimo batch (cron o "Processa coda ora").')
    return redirect('marketing:campagna-dettaglio', pk=pk)


@_staff_required
def campagna_export_csv(request, pk):
    """Export CSV degli invii della campagna, con esito conversione."""
    from datetime import timedelta

    from django.db.models import F
    from django.shortcuts import get_object_or_404
    from .models import Campagna

    campagna = get_object_or_404(Campagna, pk=pk)
    finestra = timedelta(days=campagna.finestra_conversione_giorni)
    # Convertiti in UNA query (stesso join di statistiche_campagna),
    # niente ha_convertito() per riga che farebbe N+1.
    convertiti = set(
        campagna.invii.filter(
            stato='inviato',
            cliente__ordine__stato='completato',
            cliente__ordine__data_ora__gt=F('inviato_il'),
            cliente__ordine__data_ora__lte=F('inviato_il') + finestra,
        ).values_list('cliente_id', flat=True)
    )

    oggi = timezone.localtime(timezone.now()).strftime('%Y%m%d')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="campagna_{campagna.pk}_{oggi}.csv"'
    )
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Cliente', 'Telefono', 'Stato', 'Inviato il', 'Consegna WA',
        'Motivo salto', 'Convertito',
    ])
    consegna_label = {'read': 'letto', 'delivered': 'recapitato',
                      'sent': 'inviato', 'failed': 'fallito'}
    for i in campagna.invii.select_related('cliente', 'messaggio_wa'):
        writer.writerow([
            i.cliente.nome_completo,
            i.cliente.telefono,
            i.get_stato_display(),
            timezone.localtime(i.inviato_il).strftime('%d/%m/%Y %H:%M')
            if i.inviato_il else '',
            consegna_label.get(i.messaggio_wa.stato, '')
            if i.messaggio_wa else '',
            i.motivo_salto,
            'SI' if i.cliente_id in convertiti else '',
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
            'giorni_finestra_conversione',
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

        # NB: il vecchio richiamo automatico hardcoded non esiste piu':
        # i campi richiamo_* del modello restano solo per storicita'.
        cfg.save()
        messages.success(request, 'Impostazioni salvate.')
        return redirect('marketing:impostazioni')

    return render(request, 'marketing/impostazioni.html', {'cfg': cfg})


# ---------------------------------------------------------------------
# Consulente AI (analisi e proposte via Claude API)
# ---------------------------------------------------------------------

def _prepara_proposte_campagne(analisi):
    """Arricchisce le proposte campagna dell'analisi con l'URL del
    composer precompilato, la label dei segmenti e lo stato del
    template (approvato / proposto dall'AI / da verificare)."""
    from urllib.parse import urlencode

    from django.urls import reverse
    from .services.ai import template_approvati

    approvati = {t['nome'] for t in template_approvati()}
    proposti = {t.get('nome') for t in analisi.proposte_template}

    out = []
    for p in analisi.proposte_campagne:
        chiavi = _chiavi_segmenti_valide(p.get('segmenti') or [])
        coppie = [('nome', p.get('nome', '')),
                  ('template', p.get('template_meta', ''))]
        if chiavi:
            coppie.append(('segmenti', ','.join(chiavi)))
        coppie += [('param', x) for x in (p.get('template_params') or [])]

        # Stato salvato alla generazione; per le analisi vecchie (o se
        # nel frattempo il template e' stato approvato) si ricalcola.
        stato = p.get('stato_template', 'da_verificare')
        if p.get('template_meta') in approvati:
            stato = 'approvato'
        elif p.get('template_meta') in proposti:
            stato = 'proposto'

        out.append({
            **p,
            'stato_template': stato,
            'segmenti_label': _label_chiavi(chiavi) if chiavi else '',
            'segmenti_spariti': len(chiavi) < len(p.get('segmenti') or []),
            'cta_url': reverse('marketing:campagna-nuova') + '?' + urlencode(coppie),
        })
    return out


@_staff_required
def ai_consulente(request):
    """Pagina del consulente AI: ultima analisi (o una passata via
    ?id=) con proposte azionabili + storico."""
    from .models import AnalisiAI
    from .services.ai import ai_disponibile, template_approvati

    analisi = None
    analisi_id = request.GET.get('id', '')
    if analisi_id.isdigit():
        analisi = AnalisiAI.objects.filter(pk=int(analisi_id)).first()
    if analisi is None:
        analisi = AnalisiAI.objects.first()   # ordering -creata_il

    proposte_campagne = _prepara_proposte_campagne(analisi) if analisi else []
    costo_stimato = None
    if analisi and (analisi.token_input or analisi.token_output):
        # Prezzi Claude Opus 5: 5 USD/1M input, 25 USD/1M output.
        costo_stimato = (analisi.token_input * 5 +
                         analisi.token_output * 25) / 1_000_000

    return render(request, 'marketing/ai.html', {
        'disponibile': ai_disponibile(),
        'analisi': analisi,
        'proposte_campagne': proposte_campagne,
        'storico': AnalisiAI.objects.all()[:12],
        'n_template': len(template_approvati()),
        'costo_stimato': costo_stimato,
    })


@_staff_required
def ai_genera(request):
    """POST: genera una nuova analisi AI (chiamata sincrona a Claude,
    puo' richiedere fino a un minuto)."""
    from .services.ai import genera_analisi

    if request.method != 'POST':
        return redirect('marketing:ai')
    try:
        analisi = genera_analisi(user=request.user)
    except RuntimeError as e:
        messages.error(request, str(e))
        return redirect('marketing:ai')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('analisi AI: errore inatteso')
        messages.error(request, 'Errore inatteso durante l\'analisi: '
                                'riprova tra qualche minuto.')
        return redirect('marketing:ai')
    messages.success(
        request,
        f'Analisi completata: {analisi.n_proposte} proposte. Rivedile e '
        f'attiva solo quelle che ti convincono — nulla parte da solo.')
    return redirect('marketing:ai')


@_staff_required
def ai_crea_segmento(request, pk, idx):
    """POST: crea un SegmentoPersonalizzato da una proposta dell'AI."""
    from decimal import Decimal

    from django.shortcuts import get_object_or_404
    from .models import AnalisiAI, SegmentoPersonalizzato
    from .services.ai import CRITERI_SEGMENTO

    if request.method != 'POST':
        return redirect('marketing:ai')
    analisi = get_object_or_404(AnalisiAI, pk=pk)
    try:
        proposta = analisi.proposte_segmenti[idx]
    except (IndexError, TypeError):
        messages.error(request, 'Proposta di segmento non trovata.')
        return redirect('marketing:ai')

    nome = (proposta.get('nome') or 'Segmento AI').strip()[:100]
    if SegmentoPersonalizzato.objects.filter(nome=nome).exists():
        messages.info(request, f'Il segmento "{nome}" esiste gia\'.')
        return redirect('marketing:segmenti-custom')

    seg = SegmentoPersonalizzato(
        nome=nome,
        descrizione=(proposta.get('descrizione') or '')[:200],
        attivo=True,
    )
    for campo in CRITERI_SEGMENTO:
        val = proposta.get(campo)
        if val is None:
            continue
        if campo.startswith(('lavaggi', 'giorni')):
            setattr(seg, campo, int(val))
        elif campo.startswith('frequenza'):
            setattr(seg, campo, float(val))
        else:
            setattr(seg, campo, Decimal(str(val)))
    seg.save()
    messages.success(
        request,
        f'Segmento "{seg.nome}" creato dalla proposta AI: ora e\' '
        f'selezionabile nelle campagne.')
    return redirect('marketing:segmento-custom', pk=seg.pk)


# ---------------------------------------------------------------------
# Segmenti personalizzati (criteri configurabili dall'operatore)
# ---------------------------------------------------------------------

_CAMPI_SEGMENTO_INT = ('lavaggi_min', 'lavaggi_max',
                       'giorni_ultimo_min', 'giorni_ultimo_max')
_CAMPI_SEGMENTO_FLOAT = ('frequenza_min', 'frequenza_max')
_CAMPI_SEGMENTO_DEC = ('spesa_totale_min', 'spesa_totale_max',
                       'spesa_media_min', 'spesa_media_max')


@_staff_required
def segmenti_custom(request):
    """Lista dei segmenti personalizzati con conteggio clienti."""
    from .models import SegmentoPersonalizzato
    from .services.segmentazione import (filtra_segmento_personalizzato,
                                         statistiche_clienti)
    stats = statistiche_clienti()
    righe = [
        (s, len(filtra_segmento_personalizzato(s, stats)))
        for s in SegmentoPersonalizzato.objects.all()
    ]
    return render(request, 'marketing/segmenti_custom.html', {'righe': righe})


@_staff_required
def segmento_custom_form(request, pk=None):
    """Crea/modifica un segmento personalizzato (form unico)."""
    from django.shortcuts import get_object_or_404
    from .models import SegmentoPersonalizzato

    seg = get_object_or_404(SegmentoPersonalizzato, pk=pk) if pk else None

    if request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        if not nome:
            messages.error(request, 'Il nome del segmento e\' obbligatorio.')
            return redirect(request.path)

        def _numero(campo, conv):
            raw = (request.POST.get(campo) or '').strip().replace(',', '.')
            if raw == '':
                return None
            return conv(raw)

        valori = {}
        try:
            for campo in _CAMPI_SEGMENTO_INT:
                valori[campo] = _numero(campo, int)
            for campo in _CAMPI_SEGMENTO_FLOAT:
                valori[campo] = _numero(campo, float)
            from decimal import Decimal
            for campo in _CAMPI_SEGMENTO_DEC:
                valori[campo] = _numero(campo, Decimal)
        except (ValueError, ArithmeticError):
            messages.error(request, 'Criteri non validi: usa solo numeri '
                                    '(campo vuoto = nessun filtro).')
            return redirect(request.path)

        tipo_cliente = request.POST.get('tipo_cliente') or ''
        if tipo_cliente not in ('', 'privato', 'azienda'):
            tipo_cliente = ''

        if seg is None:
            seg = SegmentoPersonalizzato()
        seg.nome = nome
        seg.descrizione = (request.POST.get('descrizione') or '').strip()
        seg.attivo = request.POST.get('attivo') == 'on'
        seg.tipo_cliente = tipo_cliente
        for campo, val in valori.items():
            setattr(seg, campo, val)
        seg.save()
        messages.success(request, f'Segmento "{seg.nome}" salvato.')
        return redirect('marketing:segmenti-custom')

    # Prefill via querystring sul form "nuovo" (usato dai CTA dei
    # consigli, es. ?spesa_totale_min=200&giorni_ultimo_min=60):
    # istanza NON salvata solo per popolare i value del template.
    pre = seg
    if seg is None and request.GET:
        pre = SegmentoPersonalizzato()
        try:
            for campo in _CAMPI_SEGMENTO_INT:
                raw = (request.GET.get(campo) or '').strip()
                if raw:
                    setattr(pre, campo, int(raw))
            for campo in _CAMPI_SEGMENTO_FLOAT:
                raw = (request.GET.get(campo) or '').strip().replace(',', '.')
                if raw:
                    setattr(pre, campo, float(raw))
            from decimal import Decimal
            for campo in _CAMPI_SEGMENTO_DEC:
                raw = (request.GET.get(campo) or '').strip().replace(',', '.')
                if raw:
                    setattr(pre, campo, Decimal(raw))
        except (ValueError, ArithmeticError):
            pre = None  # querystring malformata: form vuoto
        if pre is not None:
            pre.nome = (request.GET.get('nome') or '').strip()

    return render(request, 'marketing/segmento_custom_form.html',
                  {'seg': seg, 'v': pre})


@_staff_required
def segmento_custom_elimina(request, pk):
    from django.shortcuts import get_object_or_404
    from .models import SegmentoPersonalizzato
    if request.method != 'POST':
        return redirect('marketing:segmenti-custom')
    seg = get_object_or_404(SegmentoPersonalizzato, pk=pk)
    nome = seg.nome
    seg.delete()
    messages.success(request, f'Segmento "{nome}" eliminato. Le campagne '
                              f'gia\' inviate restano tracciate.')
    return redirect('marketing:segmenti-custom')


@_staff_required
def segmento_custom_dettaglio(request, pk):
    """Lista clienti di un segmento personalizzato (template condiviso)."""
    from django.shortcuts import get_object_or_404
    from django.urls import reverse
    from .models import SegmentoPersonalizzato
    from .services.segmentazione import filtra_segmento_personalizzato
    seg = get_object_or_404(SegmentoPersonalizzato, pk=pk)
    return render(request, 'marketing/segmento.html', {
        'label': seg.nome,
        'clienti': filtra_segmento_personalizzato(seg),
        'export_url': reverse('marketing:segmento-custom-export', args=[seg.pk]),
        'criteri': seg.criteri_leggibili(),
    })


@_staff_required
def segmento_custom_export(request, pk):
    """Export CSV del segmento personalizzato."""
    from django.shortcuts import get_object_or_404
    from .models import SegmentoPersonalizzato
    from .services.segmentazione import filtra_segmento_personalizzato
    seg = get_object_or_404(SegmentoPersonalizzato, pk=pk)
    oggi = timezone.localtime(timezone.now()).strftime('%Y%m%d')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="segmento_custom_{seg.pk}_{oggi}.csv"'
    )
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Nome', 'Telefono', 'Email', 'Ultimo lavaggio', 'Totale lavaggi',
        'Frequenza media (gg)', 'Giorni da ultimo',
        'Spesa totale', 'Spesa media',
    ])
    for cs in filtra_segmento_personalizzato(seg):
        writer.writerow([
            cs.nome_completo,
            cs.telefono,
            cs.cliente.email or '',
            timezone.localtime(cs.ultimo_lavaggio).strftime('%d/%m/%Y'),
            cs.totale_lavaggi,
            f'{cs.frequenza_media_giorni:.0f}' if cs.frequenza_media_giorni else '',
            cs.giorni_da_ultimo,
            f'{cs.totale_speso:.2f}',
            f'{cs.spesa_media:.2f}',
        ])
    return response
