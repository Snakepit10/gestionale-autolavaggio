import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum, Avg, Q, F
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.cq.models import (
    PostazioneCQ, BloccoPostazione, OperatorePostazioneTurno,
)
from apps.cq.permissions import TitolareRequiredMixin, utente_nel_gruppo
from apps.core.models import ServizioProdotto
from apps.ordini.models import Ordine, ItemOrdine
from apps.turni.models import (
    SessioneTurno, PostazioneTurno, ChecklistItem,
    ChecklistCompilata, LavorazioneOperatore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_sessione_attiva(user):
    """Restituisce la sessione turno attiva per l'utente, o None."""
    return SessioneTurno.objects.filter(
        operatore=user, stato='attivo'
    ).prefetch_related('postazioni__postazione_cq__postazione_fisica', 'postazioni__blocco').first()


def _get_postazioni_fisiche(sessione):
    """Restituisce le Postazione fisiche collegate alla sessione turno."""
    ids = set()
    for pt in sessione.postazioni.select_related('postazione_cq__postazione_fisica').all():
        if pt.postazione_cq.postazione_fisica_id:
            ids.add(pt.postazione_cq.postazione_fisica_id)
    return ids


def _get_coda_ordini(sessione):
    """
    Restituisce gli ordini in coda per le postazioni dell'operatore.
    Se nessuna PostazioneCQ ha una postazione_fisica collegata,
    mostra tutti gli ordini in coda come fallback.
    """
    base_qs = (
        ItemOrdine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione'],
            ordine__stato__in=['in_attesa', 'in_lavorazione'],
        )
        .select_related('ordine', 'ordine__cliente', 'servizio_prodotto', 'postazione_assegnata')
        .order_by('ordine__numero_progressivo')
    )

    postazioni_fisiche_ids = _get_postazioni_fisiche(sessione)
    if postazioni_fisiche_ids:
        # Filtra solo gli ordini sulle postazioni dell'operatore
        return base_qs.filter(postazione_assegnata_id__in=postazioni_fisiche_ids)
    else:
        # Fallback: nessuna postazione fisica configurata, mostra tutti gli ordini in coda
        return base_qs


def _json_ok(data=None, **kwargs):
    return JsonResponse({'ok': True, **(data or {}), **kwargs})


def _json_err(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


# ---------------------------------------------------------------------------
# Selezione postazioni
# ---------------------------------------------------------------------------

@login_required
def selezione_postazioni(request):
    # Se c'e gia una sessione attiva con checklist compilata, vai alla dashboard
    sessione = _get_sessione_attiva(request.user)
    if sessione and sessione.checklist_inizio_compilata:
        return redirect('turni:dashboard')

    if request.method == 'POST':
        selected = request.POST.getlist('postazioni')
        if not selected:
            messages.error(request, 'Seleziona almeno una postazione.')
            return redirect('turni:selezione_postazioni')

        # Chiudi eventuale sessione precedente
        SessioneTurno.objects.filter(
            operatore=request.user, stato='attivo'
        ).update(stato='chiuso', data_fine=timezone.now())

        # Crea nuova sessione
        sessione = SessioneTurno.objects.create(operatore=request.user)

        for sel in selected:
            parts = sel.split('__')
            post_id = int(parts[0])
            blocco_id = int(parts[1]) if len(parts) > 1 and parts[1] else None
            PostazioneTurno.objects.create(
                sessione=sessione,
                postazione_cq_id=post_id,
                blocco_id=blocco_id,
            )

        return redirect('turni:checklist')

    postazioni = PostazioneCQ.objects.filter(
        attiva=True
    ).prefetch_related('blocchi').order_by('ordine')

    # Conta operatori attivi per ogni postazione
    attivi = {}
    for pt in PostazioneTurno.objects.filter(sessione__stato='attivo').select_related('postazione_cq'):
        key = pt.postazione_cq_id
        attivi[key] = attivi.get(key, 0) + 1

    return render(request, 'turni/selezione_postazioni.html', {
        'postazioni': postazioni,
        'attivi': attivi,
    })


# ---------------------------------------------------------------------------
# Checklist (inizio e fine turno)
# ---------------------------------------------------------------------------

def _checklist_view_inner(request, fase):
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return redirect('turni:selezione_postazioni')

    # Determina le postazioni/blocchi del turno
    post_turno = sessione.postazioni.select_related('postazione_cq', 'blocco').all()
    post_ids = [pt.postazione_cq_id for pt in post_turno]
    blocco_ids = [pt.blocco_id for pt in post_turno if pt.blocco_id]

    # Carica checklist items per le postazioni del turno
    items_qs = ChecklistItem.objects.filter(
        attivo=True, postazione_cq_id__in=post_ids
    ).filter(
        Q(blocco__isnull=True) | Q(blocco_id__in=blocco_ids)
    ).select_related('postazione_cq', 'blocco').order_by(
        'postazione_cq__ordine', 'blocco__ordine', 'ordine'
    )

    items = list(items_qs)

    # Se non ci sono items, salta
    if not items:
        if fase == 'inizio':
            return redirect('turni:dashboard')
        else:
            return redirect('turni:chiudi_turno')

    if request.method == 'POST':
        with transaction.atomic():
            for item in items:
                esito = request.POST.get(f'esito_{item.pk}', 'ok')
                note = request.POST.get(f'note_{item.pk}', '')
                ChecklistCompilata.objects.update_or_create(
                    sessione=sessione,
                    checklist_item=item,
                    fase=fase,
                    defaults={'esito': esito, 'note': note},
                )
        if fase == 'inizio':
            messages.success(request, 'Checklist inizio turno compilata.')
            return redirect('turni:dashboard')
        else:
            messages.success(request, 'Checklist fine turno compilata.')
            return redirect('turni:chiudi_turno')

    # Raggruppa items per postazione/blocco
    grouped = {}
    for item in items:
        key = item.postazione_cq.nome
        if item.blocco:
            key = f"{item.postazione_cq.nome} › {item.blocco.nome}"
        grouped.setdefault(key, []).append(item)

    # Carica compilazioni esistenti
    compilazioni = {}
    for cc in ChecklistCompilata.objects.filter(sessione=sessione, fase=fase):
        compilazioni[cc.checklist_item_id] = cc

    return render(request, 'turni/checklist.html', {
        'sessione': sessione,
        'fase': fase,
        'grouped_items': grouped,
        'compilazioni': compilazioni,
    })


@login_required
def checklist_view(request):
    return _checklist_view_inner(request, 'inizio')


@login_required
def checklist_fine_view(request):
    return _checklist_view_inner(request, 'fine')


# ---------------------------------------------------------------------------
# Dashboard operatore
# ---------------------------------------------------------------------------

@login_required
def dashboard_operatore(request):
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return redirect('turni:selezione_postazioni')

    # Se la checklist inizio non e compilata e ci sono items, vai alla checklist
    post_turno = sessione.postazioni.select_related('postazione_cq', 'blocco').all()
    post_ids = [pt.postazione_cq_id for pt in post_turno]

    has_checklist = ChecklistItem.objects.filter(
        attivo=True, postazione_cq_id__in=post_ids
    ).exists()

    if has_checklist and not sessione.checklist_inizio_compilata:
        return redirect('turni:checklist')

    coda = _get_coda_ordini(sessione)

    # Raggruppa per ordine
    ordini_dict = {}
    for item in coda:
        ordine = item.ordine
        if ordine.pk not in ordini_dict:
            ordini_dict[ordine.pk] = {
                'ordine': ordine,
                'items': [],
            }
        ordini_dict[ordine.pk]['items'].append(item)
    ordini_coda = list(ordini_dict.values())

    # Lavorazione attiva
    lavorazione_attiva = LavorazioneOperatore.objects.filter(
        sessione=sessione, stato__in=['in_lavorazione', 'in_pausa']
    ).select_related('ordine', 'postazione_cq', 'blocco').first()

    # Servizi per il form aggiungi item (tutti i servizi attivi, raggruppati per categoria)
    from apps.core.models import Categoria
    categorie_servizi = []
    for cat in Categoria.objects.filter(attiva=True).order_by('ordine_visualizzazione'):
        servizi = list(cat.servizioprodotto_set.filter(attivo=True).values(
            'id', 'titolo', 'prezzo', 'tipo',
        ))
        if servizi:
            categorie_servizi.append({'nome': cat.nome, 'servizi': servizi})

    return render(request, 'turni/dashboard_operatore.html', {
        'sessione': sessione,
        'postazioni_turno': post_turno,
        'ordini_coda': ordini_coda,
        'lavorazione_attiva': lavorazione_attiva,
        'categorie_servizi_json': json.dumps(categorie_servizi, default=str),
    })


# ---------------------------------------------------------------------------
# Chiudi turno
# ---------------------------------------------------------------------------

@login_required
def chiudi_turno(request):
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return redirect('turni:selezione_postazioni')

    if request.method == 'POST':
        sessione.chiudi()
        messages.success(request, 'Turno chiuso correttamente.')
        return redirect('turni:selezione_postazioni')

    # GET: mostra conferma o redirect alla checklist fine
    has_checklist = ChecklistItem.objects.filter(
        attivo=True,
        postazione_cq_id__in=[pt.postazione_cq_id for pt in sessione.postazioni.all()]
    ).exists()

    if has_checklist and not sessione.checklist_fine_compilata:
        return redirect('turni:checklist_fine')

    # Mostra pagina conferma chiusura
    return render(request, 'turni/chiudi_turno_confirm.html', {
        'sessione': sessione,
    })


# ---------------------------------------------------------------------------
# API: Ordine dettaglio
# ---------------------------------------------------------------------------

@login_required
def api_ordine_dettaglio(request, ordine_id):
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    items = list(ordine.items.select_related('servizio_prodotto', 'aggiunto_da').values(
        'id', 'servizio_prodotto__titolo', 'quantita',
        'prezzo_unitario', 'stato', 'aggiunto_da__id',
    ))
    cliente_nome = ''
    if ordine.cliente:
        cliente_nome = str(ordine.cliente)

    # Lavorazioni per questo ordine (dell'operatore corrente)
    sessione = _get_sessione_attiva(request.user)
    lavorazione = None
    if sessione:
        lav = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine
        ).exclude(stato='completato').first()
        if lav:
            lavorazione = {
                'id': lav.pk,
                'stato': lav.stato,
                'inizio': lav.inizio.isoformat(),
                'pausa_inizio': lav.pausa_inizio.isoformat() if lav.pausa_inizio else None,
                'tempo_pausa_sec': lav.tempo_pausa_totale.total_seconds(),
            }

    # Fasi completate (da tutti gli operatori)
    fasi_completate = []
    for lav_c in LavorazioneOperatore.objects.filter(
        ordine=ordine, stato='completato'
    ).select_related('postazione_cq', 'sessione__operatore').order_by('inizio'):
        op = lav_c.sessione.operatore
        fasi_completate.append({
            'postazione': lav_c.postazione_cq.nome,
            'operatore': op.get_full_name() or op.username,
            'tempo_min': round(lav_c.tempo_lavoro_netto_minuti, 1),
        })

    return _json_ok({
        'ordine': {
            'id': ordine.pk,
            'numero': ordine.numero_progressivo,
            'cliente': cliente_nome,
            'tipo_auto': ordine.tipo_auto or '',
            'nota': ordine.nota or '',
            'stato': ordine.stato,
            'items': items,
        },
        'lavorazione': lavorazione,
        'fasi_completate': fasi_completate,
    })


# ---------------------------------------------------------------------------
# API: Inizia lavoro
# ---------------------------------------------------------------------------

@login_required
@transaction.atomic
def api_inizia_lavoro(request, ordine_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)

    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return _json_err('Nessuna sessione turno attiva.')

    # Verifica che non ci sia gia una lavorazione in corso
    in_corso = LavorazioneOperatore.objects.filter(
        sessione=sessione, stato__in=['in_lavorazione', 'in_pausa']
    ).first()
    if in_corso:
        return _json_err('Hai gia una lavorazione in corso. Completala prima.')

    ordine = get_object_or_404(Ordine, pk=ordine_id)

    # Trova la postazione CQ dell'operatore per questo ordine
    post_turno = sessione.postazioni.select_related(
        'postazione_cq__postazione_fisica', 'blocco'
    ).all()

    postazione_cq = None
    blocco = None

    # Prima prova: match per postazione fisica collegata
    for pt in post_turno:
        if pt.postazione_cq.postazione_fisica_id:
            has_items = ordine.items.filter(
                postazione_assegnata_id=pt.postazione_cq.postazione_fisica_id,
                stato__in=['in_attesa', 'in_lavorazione'],
            ).exists()
            if has_items:
                postazione_cq = pt.postazione_cq
                blocco = pt.blocco
                break

    # Fallback: se nessuna postazione fisica configurata, usa la prima postazione del turno
    if not postazione_cq:
        has_pending = ordine.items.filter(stato__in=['in_attesa', 'in_lavorazione']).exists()
        if has_pending and post_turno.exists():
            first_pt = post_turno.first()
            postazione_cq = first_pt.postazione_cq
            blocco = first_pt.blocco

    if not postazione_cq:
        return _json_err('Nessun item lavorabile in questo ordine.')

    # Crea LavorazioneOperatore
    lav = LavorazioneOperatore.objects.create(
        sessione=sessione,
        ordine=ordine,
        postazione_cq=postazione_cq,
        blocco=blocco,
    )

    # Aggiorna ItemOrdine in_attesa → in_lavorazione
    if postazione_cq.postazione_fisica:
        items_da_avviare = ordine.items.filter(
            postazione_assegnata=postazione_cq.postazione_fisica,
            stato='in_attesa',
        )
    else:
        # Fallback: avvia tutti gli items in_attesa dell'ordine
        items_da_avviare = ordine.items.filter(stato='in_attesa')
    now = timezone.now()
    for item in items_da_avviare:
        item.stato = 'in_lavorazione'
        item.inizio_lavorazione = now
        item.save(update_fields=['stato', 'inizio_lavorazione'])

    # Aggiorna stato ordine
    if ordine.stato == 'in_attesa':
        ordine.stato = 'in_lavorazione'
        ordine.save(update_fields=['stato'])

    # CQ integration: auto-crea OperatorePostazioneTurno
    OperatorePostazioneTurno.objects.get_or_create(
        ordine=ordine,
        postazione=postazione_cq.codice,
        blocco_codice=blocco.codice if blocco else '',
        operatore=request.user,
    )

    return _json_ok({
        'lavorazione': {
            'id': lav.pk,
            'stato': lav.stato,
            'inizio': lav.inizio.isoformat(),
        },
    })


# ---------------------------------------------------------------------------
# API: Pausa / Riprendi / Completa
# ---------------------------------------------------------------------------

@login_required
def api_pausa_lavoro(request, lav_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)
    lav = get_object_or_404(LavorazioneOperatore, pk=lav_id, sessione__operatore=request.user)
    if lav.stato != 'in_lavorazione':
        return _json_err('Lavorazione non in corso.')
    lav.avvia_pausa()
    return _json_ok({'stato': lav.stato})


@login_required
def api_riprendi_lavoro(request, lav_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)
    lav = get_object_or_404(LavorazioneOperatore, pk=lav_id, sessione__operatore=request.user)
    if lav.stato != 'in_pausa':
        return _json_err('Lavorazione non in pausa.')
    lav.riprendi()
    return _json_ok({'stato': lav.stato})


@login_required
@transaction.atomic
def api_completa_lavoro(request, lav_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)
    lav = get_object_or_404(
        LavorazioneOperatore, pk=lav_id, sessione__operatore=request.user
    )
    if lav.stato == 'completato':
        return _json_err('Lavorazione gia completata.')

    lav.completa()

    # Aggiorna gli ItemOrdine della postazione fisica
    ordine = lav.ordine
    if lav.postazione_cq.postazione_fisica:
        items = ordine.items.filter(
            postazione_assegnata=lav.postazione_cq.postazione_fisica,
            stato='in_lavorazione',
        )
    else:
        # Fallback: completa tutti gli items in_lavorazione dell'ordine
        items = ordine.items.filter(stato='in_lavorazione')

    now = timezone.now()
    for item in items:
        item.stato = 'completato'
        item.fine_lavorazione = now
        item.save(update_fields=['stato', 'fine_lavorazione'])

    # Verifica se tutti gli items dell'ordine sono completati
    if not ordine.items.exclude(stato='completato').exists():
        ordine.stato = 'completato'
        ordine.save(update_fields=['stato'])

    return _json_ok({
        'stato': lav.stato,
        'tempo_netto_min': round(lav.tempo_lavoro_netto_minuti, 1),
    })


# ---------------------------------------------------------------------------
# API: Aggiungi item (supplemento sporco eccessivo)
# ---------------------------------------------------------------------------

@login_required
@transaction.atomic
def api_aggiungi_item(request, ordine_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_err('JSON non valido')

    ordine = get_object_or_404(Ordine, pk=ordine_id)
    if ordine.stato == 'annullato':
        return _json_err('Ordine annullato.')

    servizio_id = data.get('servizio_id')
    quantita = int(data.get('quantita', 1))
    prezzo = data.get('prezzo')
    servizio = get_object_or_404(ServizioProdotto, pk=servizio_id, attivo=True)

    if quantita < 1:
        return _json_err('Quantita deve essere almeno 1.')

    prezzo_unitario = float(prezzo) if prezzo is not None else float(servizio.prezzo)

    # Trova la postazione fisica dell'operatore
    sessione = _get_sessione_attiva(request.user)
    postazione_fisica = None
    if sessione:
        for pt in sessione.postazioni.select_related('postazione_cq__postazione_fisica').all():
            if pt.postazione_cq.postazione_fisica:
                postazione_fisica = pt.postazione_cq.postazione_fisica
                break

    item = ItemOrdine.objects.create(
        ordine=ordine,
        servizio_prodotto=servizio,
        quantita=quantita,
        prezzo_unitario=prezzo_unitario,
        postazione_assegnata=postazione_fisica,
        aggiunto_da=request.user,
    )

    # Ricalcola totale ordine
    totale = sum(i.prezzo_unitario * i.quantita for i in ordine.items.all())
    ordine.totale = totale
    sconto = ordine.importo_sconto or 0
    ordine.totale_finale = totale - sconto
    ordine.save(update_fields=['totale', 'totale_finale'])

    return _json_ok({
        'item_id': item.pk,
        'servizio': servizio.titolo,
        'prezzo': str(servizio.prezzo),
        'totale_ordine': str(ordine.totale_finale),
    })


# ---------------------------------------------------------------------------
# API: Coda ordini
# ---------------------------------------------------------------------------

@login_required
def api_coda_ordini(request):
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return _json_ok({'ordini': []})

    coda = _get_coda_ordini(sessione)

    ordini_dict = {}
    for item in coda:
        ordine = item.ordine
        if ordine.pk not in ordini_dict:
            ordini_dict[ordine.pk] = {
                'id': ordine.pk,
                'numero': ordine.numero_progressivo,
                'cliente': str(ordine.cliente) if ordine.cliente else '',
                'tipo_auto': ordine.tipo_auto or '',
                'stato': ordine.stato,
                'items_count': 0,
                'servizi': [],
            }
        ordini_dict[ordine.pk]['items_count'] += 1
        ordini_dict[ordine.pk]['servizi'].append(item.servizio_prodotto.titolo)

    return _json_ok({'ordini': list(ordini_dict.values())})


# ---------------------------------------------------------------------------
# Configurazione Checklist (CRUD titolare)
# ---------------------------------------------------------------------------

@login_required
def config_checklist(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        messages.error(request, 'Accesso riservato al titolare.')
        return redirect('core:home')

    items = ChecklistItem.objects.select_related(
        'postazione_cq', 'blocco'
    ).order_by('postazione_cq__ordine', 'blocco__ordine', 'ordine')

    postazioni = PostazioneCQ.objects.filter(attiva=True).prefetch_related('blocchi').order_by('ordine')

    return render(request, 'turni/config_checklist.html', {
        'items': items,
        'postazioni': postazioni,
    })


@login_required
def api_salva_checklist_item(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)

    pk = request.POST.get('id')
    postazione_cq_id = request.POST.get('postazione_cq_id')
    blocco_id = request.POST.get('blocco_id') or None
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    attivo = request.POST.get('attivo', '1') == '1'

    if not nome or not postazione_cq_id:
        return _json_err('Nome e postazione obbligatori.')

    postazione = get_object_or_404(PostazioneCQ, pk=postazione_cq_id)

    if pk:
        obj = get_object_or_404(ChecklistItem, pk=pk)
        obj.postazione_cq = postazione
        obj.blocco_id = blocco_id if blocco_id else None
        obj.nome = nome
        obj.ordine = ordine
        obj.attivo = attivo
        obj.save()
    else:
        obj = ChecklistItem.objects.create(
            postazione_cq=postazione,
            blocco_id=blocco_id if blocco_id else None,
            nome=nome, ordine=ordine, attivo=attivo,
        )
    return _json_ok({
        'id': obj.pk, 'nome': obj.nome,
        'postazione': obj.postazione_cq.nome,
        'blocco': obj.blocco.nome if obj.blocco else '',
    })


@login_required
def api_elimina_checklist_item(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(ChecklistItem, pk=pk)
    obj.delete()
    return _json_ok()


# ---------------------------------------------------------------------------
# Report lavorazioni
# ---------------------------------------------------------------------------

@login_required
def report_lavorazioni(request):
    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        messages.error(request, 'Accesso riservato.')
        return redirect('core:home')

    oggi = date.today()
    periodo = request.GET.get('periodo', 'mese')
    if periodo == 'settimana':
        data_inizio = oggi - timedelta(days=7)
    elif periodo == 'trimestre':
        data_inizio = oggi - timedelta(days=90)
    else:
        data_inizio = oggi.replace(day=1)

    lavorazioni = LavorazioneOperatore.objects.filter(
        stato='completato',
        inizio__date__gte=data_inizio,
    ).select_related(
        'sessione__operatore', 'ordine', 'postazione_cq', 'blocco',
    )

    # Statistiche globali
    totale_ordini = lavorazioni.values('ordine').distinct().count()
    totale_lavorazioni = lavorazioni.count()

    # Tempo medio per postazione
    tempi_per_postazione = {}
    tempi_per_operatore = {}
    ordini_tempi = {}

    for lav in lavorazioni:
        minuti = lav.tempo_lavoro_netto_minuti
        post_nome = lav.postazione_cq.nome
        op = lav.sessione.operatore

        tempi_per_postazione.setdefault(post_nome, []).append(minuti)

        op_nome = op.get_full_name() or op.username
        tempi_per_operatore.setdefault(op_nome, {'minuti': [], 'ordini': set()})
        tempi_per_operatore[op_nome]['minuti'].append(minuti)
        tempi_per_operatore[op_nome]['ordini'].add(lav.ordine_id)

        ordini_tempi.setdefault(lav.ordine_id, {
            'ordine': lav.ordine,
            'fasi': [],
            'tempo_totale': 0,
            'operatori': set(),
        })
        ordini_tempi[lav.ordine_id]['fasi'].append({
            'postazione': post_nome,
            'operatore': op_nome,
            'minuti': round(minuti, 1),
        })
        ordini_tempi[lav.ordine_id]['tempo_totale'] += minuti
        ordini_tempi[lav.ordine_id]['operatori'].add(op_nome)

    # Statistiche postazione
    stats_postazione = []
    for nome, minuti_list in tempi_per_postazione.items():
        stats_postazione.append({
            'nome': nome,
            'media': round(sum(minuti_list) / len(minuti_list), 1),
            'totale': round(sum(minuti_list), 1),
            'count': len(minuti_list),
        })

    # Statistiche operatore
    stats_operatore = []
    for nome, dati in tempi_per_operatore.items():
        ml = dati['minuti']
        stats_operatore.append({
            'nome': nome,
            'n_ordini': len(dati['ordini']),
            'tempo_medio': round(sum(ml) / len(ml), 1),
            'tempo_totale': round(sum(ml), 1),
        })

    # Ordini dettaglio
    ordini_list = sorted(ordini_tempi.values(), key=lambda x: x['ordine'].data_ora, reverse=True)
    for o in ordini_list:
        o['tempo_totale'] = round(o['tempo_totale'], 1)
        o['operatori'] = ', '.join(o['operatori'])

    return render(request, 'turni/report_lavorazioni.html', {
        'periodo': periodo,
        'data_inizio': data_inizio,
        'totale_ordini': totale_ordini,
        'totale_lavorazioni': totale_lavorazioni,
        'stats_postazione': stats_postazione,
        'stats_postazione_json': json.dumps(stats_postazione),
        'stats_operatore': stats_operatore,
        'ordini_list': ordini_list[:50],
    })


@login_required
def report_ordine_dettaglio(request, ordine_id):
    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        return redirect('core:home')

    ordine = get_object_or_404(Ordine, pk=ordine_id)
    lavorazioni = LavorazioneOperatore.objects.filter(
        ordine=ordine, stato='completato'
    ).select_related('sessione__operatore', 'postazione_cq', 'blocco').order_by('inizio')

    fasi = []
    tempo_totale = timedelta(0)
    for lav in lavorazioni:
        tempo_netto = lav.tempo_lavoro_netto
        fasi.append({
            'postazione': lav.postazione_cq.nome,
            'blocco': lav.blocco.nome if lav.blocco else '',
            'operatore': lav.sessione.operatore.get_full_name() or lav.sessione.operatore.username,
            'inizio': lav.inizio,
            'fine': lav.fine,
            'pausa_min': round(lav.tempo_pausa_totale.total_seconds() / 60, 1),
            'tempo_netto_min': round(tempo_netto.total_seconds() / 60, 1),
        })
        tempo_totale += tempo_netto

    return render(request, 'turni/report_ordine_dettaglio.html', {
        'ordine': ordine,
        'fasi': fasi,
        'tempo_totale_min': round(tempo_totale.total_seconds() / 60, 1),
    })


@login_required
def api_report_dati(request):
    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        return _json_err('Non autorizzato', 403)
    # Placeholder for advanced chart data
    return _json_ok({'message': 'TODO'})
