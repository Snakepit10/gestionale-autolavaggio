import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models as db_models, transaction
from django.db.models import Count, Sum, Avg, Q, F, Case, When, IntegerField
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
    CategoriaChecklist, EsitoChecklist, VerificaChecklist,
    SegnalazioneDifetto,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_sessione_attiva(user):
    """Restituisce la sessione turno attiva per l'utente, o None."""
    return SessioneTurno.objects.filter(
        operatore=user, stato='attivo'
    ).prefetch_related('postazioni__postazione_cq', 'postazioni__blocco').first()


def _get_coda_ordini(sessione):
    """
    Restituisce gli items in coda (coda unica).
    Ogni ordine appare nella coda di tutti gli operatori.
    """
    return (
        ItemOrdine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione'],
            ordine__stato__in=['in_attesa', 'in_lavorazione'],
            servizio_prodotto__tipo='servizio',
        )
        .select_related('ordine', 'ordine__cliente', 'servizio_prodotto')
        .annotate(
            has_priority=Case(
                When(ordine__priorita__gt=0, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by('has_priority', 'ordine__priorita', 'ordine__data_ora')
    )


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
    ).select_related('postazione_cq', 'blocco', 'categoria').order_by(
        'postazione_cq__ordine', 'blocco__ordine', 'categoria__ordine', 'ordine'
    )

    items = list(items_qs)

    # Se non ci sono items, salta
    if not items:
        if fase == 'inizio':
            return redirect('turni:dashboard')
        else:
            return redirect('turni:chiudi_turno')

    # Mappa esiti per categoria
    esiti_map = {}
    for cat in CategoriaChecklist.objects.prefetch_related('esiti').all():
        esiti_map[cat.pk] = list(cat.esiti.order_by('ordine'))

    # Aggiungi esiti disponibili a ogni item
    for item in items:
        item.esiti_disponibili = esiti_map.get(item.categoria_id, [])

    if request.method == 'POST':
        with transaction.atomic():
            for item in items:
                esito_pk = request.POST.get(f'esito_{item.pk}', '')
                note = request.POST.get(f'note_{item.pk}', '')
                esito_obj = None
                esito_str = 'ok'
                if esito_pk:
                    try:
                        esito_obj = EsitoChecklist.objects.get(pk=esito_pk)
                        # Mappa a stringa per backward compatibility
                        if esito_obj.codice == 'ok':
                            esito_str = 'ok'
                        elif esito_obj.codice == 'na':
                            esito_str = 'na'
                        else:
                            esito_str = 'non_ok'
                    except EsitoChecklist.DoesNotExist:
                        pass
                ChecklistCompilata.objects.update_or_create(
                    sessione=sessione,
                    checklist_item=item,
                    fase=fase,
                    defaults={'esito': esito_str, 'esito_obj': esito_obj, 'note': note},
                )
        if fase == 'inizio':
            messages.success(request, 'Checklist inizio turno compilata.')
            return redirect('turni:dashboard')
        else:
            messages.success(request, 'Checklist fine turno compilata.')
            return redirect('turni:chiudi_turno')

    # Raggruppa items: postazione > categoria > items
    from collections import OrderedDict
    grouped = OrderedDict()
    for item in items:
        post_key = item.postazione_cq.nome
        if item.blocco:
            post_key = f"{item.postazione_cq.nome} › {item.blocco.nome}"
        if post_key not in grouped:
            grouped[post_key] = OrderedDict()

        cat = item.categoria
        cat_key = cat.nome if cat else 'Altro'
        if cat_key not in grouped[post_key]:
            grouped[post_key][cat_key] = {
                'nome': cat_key,
                'icona': cat.icona if cat else '',
                'items': [],
            }
        grouped[post_key][cat_key]['items'].append(item)

    # Carica compilazioni esistenti
    compilazioni = {}
    for cc in ChecklistCompilata.objects.filter(
        sessione=sessione, fase=fase
    ).select_related('esito_obj'):
        compilazioni[cc.checklist_item_id] = cc

    return render(request, 'turni/checklist.html', {
        'sessione': sessione,
        'fase': fase,
        'grouped': grouped,
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

    # Zone e difetti mappati alle postazioni dell'operatore (per segnalazione difetti)
    from apps.cq.models import ZonaConfig, ZonaDifettoMapping
    operatore_post_codici = [pt.postazione_cq.codice for pt in post_turno]
    zone_operatore = ZonaConfig.objects.filter(
        attiva=True, postazione_produttore__in=operatore_post_codici
    ).prefetch_related('difetti_config__tipo_difetto').select_related('categoria')

    zone_difetti = []
    for zona in zone_operatore:
        tipi = [
            {'codice': m.tipo_difetto.codice, 'nome': m.tipo_difetto.nome}
            for m in zona.difetti_config.select_related('tipo_difetto').all()
            if m.tipo_difetto.attivo
        ]
        if tipi:
            zone_difetti.append({
                'codice': zona.codice,
                'nome': zona.nome,
                'categoria': zona.categoria.nome if zona.categoria else '',
                'tipi': tipi,
            })

    return render(request, 'turni/dashboard_operatore.html', {
        'sessione': sessione,
        'postazioni_turno': post_turno,
        'ordini_coda': ordini_coda,
        'lavorazione_attiva': lavorazione_attiva,
        'categorie_servizi_json': json.dumps(categorie_servizi, default=str),
        'zone_difetti_json': json.dumps(zone_difetti),
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

    # Lavorazioni attive per questo ordine (dell'operatore corrente, una per blocco)
    sessione = _get_sessione_attiva(request.user)
    lavorazione = None  # Prima lavorazione attiva (per il timer principale)
    lavorazioni_attive = []
    if sessione:
        lavs = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine
        ).exclude(stato='completato').select_related('postazione_cq', 'blocco')
        for lav in lavs:
            lav_data = {
                'id': lav.pk,
                'stato': lav.stato,
                'inizio': lav.inizio.isoformat(),
                'pausa_inizio': lav.pausa_inizio.isoformat() if lav.pausa_inizio else None,
                'tempo_pausa_sec': lav.tempo_pausa_totale.total_seconds(),
                'postazione': lav.postazione_cq.nome,
                'blocco': lav.blocco.nome if lav.blocco else '',
            }
            lavorazioni_attive.append(lav_data)
            if not lavorazione:
                lavorazione = lav_data

    # Fasi completate (da tutti gli operatori)
    # Fasi completate (da tutti gli operatori, con info blocco)
    fasi_completate = []
    for lav_c in LavorazioneOperatore.objects.filter(
        ordine=ordine, stato='completato'
    ).select_related('postazione_cq', 'blocco', 'sessione__operatore').order_by('inizio'):
        op = lav_c.sessione.operatore
        label = lav_c.postazione_cq.sigla or lav_c.postazione_cq.nome
        if lav_c.blocco:
            label += f" [{lav_c.blocco.nome}]"
        fasi_completate.append({
            'postazione': label,
            'operatore': op.get_full_name() or op.username,
            'tempo_min': round(lav_c.tempo_lavoro_netto_minuti, 1),
        })

    # Stato postazioni
    stati_postazioni = ordine.get_stati_postazioni()

    # Segnalazioni difetti gia fatte per questo ordine
    segnalazioni = list(SegnalazioneDifetto.objects.filter(
        ordine=ordine
    ).values('zona', 'tipo_difetto', 'gravita', 'note', 'operatore__first_name', 'operatore__username'))

    return _json_ok({
        'ordine': {
            'id': ordine.pk,
            'numero': ordine.numero_progressivo,
            'cliente': cliente_nome,
            'tipo_auto': ordine.tipo_auto or '',
            'nota': ordine.nota or '',
            'stato': ordine.stato,
            'tipo_consegna': ordine.tipo_consegna or '',
            'ora_consegna': ordine.ora_consegna_richiesta.strftime('%H:%M') if ordine.ora_consegna_richiesta else '',
            'items': items,
            'stati_postazioni': stati_postazioni,
        },
        'lavorazione': lavorazione,
        'lavorazioni_attive': lavorazioni_attive,
        'fasi_completate': fasi_completate,
        'segnalazioni': segnalazioni,
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

    # Trova le postazioni/blocchi del turno dell'operatore
    post_turno = list(sessione.postazioni.select_related('postazione_cq', 'blocco').all())
    if not post_turno:
        return _json_err('Nessuna postazione assegnata al turno.')

    # Crea una LavorazioneOperatore per OGNI blocco/postazione del turno
    # (se l'operatore ha scelto 3 blocchi, crea 3 lavorazioni)
    lavorazioni_create = []
    for pt in post_turno:
        # Evita duplicati: non creare se esiste gia una lavorazione non completata
        existing = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine,
            postazione_cq=pt.postazione_cq, blocco=pt.blocco,
        ).exclude(stato='completato').first()
        if existing:
            lavorazioni_create.append(existing)
            continue

        lav = LavorazioneOperatore.objects.create(
            sessione=sessione,
            ordine=ordine,
            postazione_cq=pt.postazione_cq,
            blocco=pt.blocco,
        )
        lavorazioni_create.append(lav)

        # CQ integration: auto-crea OperatorePostazioneTurno per ogni blocco
        OperatorePostazioneTurno.objects.get_or_create(
            ordine=ordine,
            postazione=pt.postazione_cq.codice,
            blocco_codice=pt.blocco.codice if pt.blocco else '',
            operatore=request.user,
        )

    if not lavorazioni_create:
        return _json_err('Nessun item lavorabile in questo ordine.')

    # Aggiorna stato ordine
    if ordine.stato == 'in_attesa':
        ordine.stato = 'in_lavorazione'
        ordine.save(update_fields=['stato'])

    # Restituisci la prima lavorazione come riferimento
    lav = lavorazioni_create[0]

    return _json_ok({
        'lavorazione': {
            'id': lav.pk,
            'stato': lav.stato,
            'inizio': lav.inizio.isoformat(),
        },
        'lavorazioni_count': len(lavorazioni_create),
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

    # Nel flusso coda unica, completare una fase NON completa l'ordine.
    # L'ordine resta in_lavorazione finche TUTTE le PostazioneCQ attive
    # (non controllo_finale) hanno una LavorazioneOperatore completata.
    ordine = lav.ordine

    # Aggiorna stato ordine a in_lavorazione se era in_attesa
    if ordine.stato == 'in_attesa':
        ordine.stato = 'in_lavorazione'
        ordine.save(update_fields=['stato'])

    # Verifica se tutte le postazioni (e tutti i blocchi) hanno completato
    from apps.cq.models import PostazioneCQ
    tutte_completate = True
    for pcq in PostazioneCQ.objects.filter(attiva=True, is_controllo_finale=False).prefetch_related('blocchi'):
        blocchi = list(pcq.blocchi.all())
        if blocchi:
            # Postazione con blocchi: ogni blocco deve avere una lavorazione completata
            for blocco in blocchi:
                if not LavorazioneOperatore.objects.filter(
                    ordine=ordine, postazione_cq=pcq, blocco=blocco, stato='completato'
                ).exists():
                    tutte_completate = False
                    break
        else:
            # Postazione senza blocchi: deve avere almeno una lavorazione completata
            if not LavorazioneOperatore.objects.filter(
                ordine=ordine, postazione_cq=pcq, stato='completato'
            ).exists():
                tutte_completate = False
        if not tutte_completate:
            break

    if tutte_completate:
        ordine.stato = 'completato'
        ordine.save(update_fields=['stato'])
        now = timezone.now()
        for item in ordine.items.exclude(stato='completato'):
            item.stato = 'completato'
            item.fine_lavorazione = now
            item.save(update_fields=['stato', 'fine_lavorazione'])

    return _json_ok({
        'stato': lav.stato,
        'tempo_netto_min': round(lav.tempo_lavoro_netto_minuti, 1),
        'ordine_completato': ordine.stato == 'completato',
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

    # Trova la PostazioneCQ dell'operatore
    sessione = _get_sessione_attiva(request.user)
    pcq = None
    if sessione:
        pt = sessione.postazioni.select_related('postazione_cq').first()
        if pt:
            pcq = pt.postazione_cq

    item = ItemOrdine.objects.create(
        ordine=ordine,
        servizio_prodotto=servizio,
        quantita=quantita,
        prezzo_unitario=prezzo_unitario,
        postazione_cq=pcq,
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
# API: Segnalazione difetto
# ---------------------------------------------------------------------------

@login_required
@transaction.atomic
def api_segnala_difetto(request, ordine_id):
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_err('JSON non valido')

    ordine = get_object_or_404(Ordine, pk=ordine_id)
    zona = data.get('zona', '')
    tipo_difetto = data.get('tipo_difetto', '')
    gravita = data.get('gravita', 'media')
    note = data.get('note', '')

    if not zona or not tipo_difetto:
        return _json_err('Zona e tipo difetto obbligatori.')

    sessione = _get_sessione_attiva(request.user)
    postazione_cq = None
    if sessione:
        pt = sessione.postazioni.select_related('postazione_cq').first()
        if pt:
            postazione_cq = pt.postazione_cq

    if not postazione_cq:
        return _json_err('Nessuna postazione assegnata.')

    segnalazione = SegnalazioneDifetto.objects.create(
        ordine=ordine,
        zona=zona,
        tipo_difetto=tipo_difetto,
        gravita=gravita,
        postazione_cq=postazione_cq,
        operatore=request.user,
        note=note,
    )
    return _json_ok({
        'id': segnalazione.pk,
        'zona': segnalazione.zona_nome,
        'tipo_difetto': segnalazione.tipo_difetto_nome,
    })


# ---------------------------------------------------------------------------
# Configurazione Checklist (CRUD titolare)
# ---------------------------------------------------------------------------

@login_required
def config_checklist(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        messages.error(request, 'Accesso riservato al titolare.')
        return redirect('core:home')

    items = ChecklistItem.objects.select_related(
        'postazione_cq', 'blocco', 'categoria'
    ).order_by('postazione_cq__ordine', 'blocco__ordine', 'categoria__ordine', 'ordine')

    postazioni = PostazioneCQ.objects.filter(attiva=True).prefetch_related('blocchi').order_by('ordine')
    categorie = CategoriaChecklist.objects.prefetch_related('esiti').order_by('ordine')

    return render(request, 'turni/config_checklist.html', {
        'items': items,
        'postazioni': postazioni,
        'categorie': categorie,
    })


@login_required
def api_salva_checklist_item(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)

    pk = request.POST.get('id')
    postazione_cq_id = request.POST.get('postazione_cq_id')
    blocco_id = request.POST.get('blocco_id') or None
    categoria_id = request.POST.get('categoria_id') or None
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
        obj.categoria_id = categoria_id if categoria_id else None
        obj.nome = nome
        obj.ordine = ordine
        obj.attivo = attivo
        obj.save()
    else:
        obj = ChecklistItem.objects.create(
            postazione_cq=postazione,
            blocco_id=blocco_id if blocco_id else None,
            categoria_id=categoria_id if categoria_id else None,
            nome=nome, ordine=ordine, attivo=attivo,
        )
    return _json_ok({
        'id': obj.pk, 'nome': obj.nome,
        'postazione': obj.postazione_cq.nome,
        'blocco': obj.blocco.nome if obj.blocco else '',
        'categoria': obj.categoria.nome if obj.categoria else '',
    })


@login_required
def api_elimina_checklist_item(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(ChecklistItem, pk=pk)
    obj.delete()
    return _json_ok()


# ---------------------------------------------------------------------------
# CRUD Categorie e Esiti Checklist 5S (titolare)
# ---------------------------------------------------------------------------

@login_required
def api_salva_categoria(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    icona = request.POST.get('icona', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    if not nome:
        return _json_err('Nome obbligatorio.')
    if pk:
        obj = get_object_or_404(CategoriaChecklist, pk=pk)
        obj.nome = nome
        obj.icona = icona
        obj.ordine = ordine
        obj.save()
    else:
        obj = CategoriaChecklist.objects.create(nome=nome, icona=icona, ordine=ordine)
    return _json_ok({'id': obj.pk, 'nome': obj.nome})


@login_required
def api_elimina_categoria(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(CategoriaChecklist, pk=pk)
    if obj.checklist_items.exists():
        return _json_err('Impossibile eliminare: ci sono voci checklist collegate.')
    obj.delete()
    return _json_ok()


@login_required
def api_salva_esito(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    categoria_id = request.POST.get('categoria_id')
    codice = request.POST.get('codice', '').strip()
    nome = request.POST.get('nome', '').strip()
    colore = request.POST.get('colore', 'secondary').strip()
    ordine = int(request.POST.get('ordine', 0))
    if not nome or not codice or not categoria_id:
        return _json_err('Categoria, codice e nome obbligatori.')
    cat = get_object_or_404(CategoriaChecklist, pk=categoria_id)
    if pk:
        obj = get_object_or_404(EsitoChecklist, pk=pk)
        obj.categoria = cat
        obj.codice = codice
        obj.nome = nome
        obj.colore = colore
        obj.ordine = ordine
        obj.save()
    else:
        obj = EsitoChecklist.objects.create(
            categoria=cat, codice=codice, nome=nome, colore=colore, ordine=ordine,
        )
    return _json_ok({'id': obj.pk, 'nome': obj.nome})


@login_required
def api_elimina_esito(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(EsitoChecklist, pk=pk)
    if obj.compilazioni.exists():
        return _json_err('Impossibile eliminare: ci sono compilazioni che usano questo esito.')
    obj.delete()
    return _json_ok()


# ---------------------------------------------------------------------------
# Storico Checklist (responsabile/titolare)
# ---------------------------------------------------------------------------

@login_required
def storico_checklist(request):
    if not utente_nel_gruppo(request.user, 'responsabile', 'titolare'):
        messages.error(request, 'Accesso riservato.')
        return redirect('core:home')

    oggi = date.today()
    periodo = request.GET.get('periodo', 'mese')
    operatore_id = request.GET.get('operatore')
    postazione_id = request.GET.get('postazione')

    if periodo == 'settimana':
        data_inizio = oggi - timedelta(days=7)
    elif periodo == 'trimestre':
        data_inizio = oggi - timedelta(days=90)
    else:
        data_inizio = oggi.replace(day=1)

    # Query base — SOLO problemi (esito != ok e != na)
    qs = ChecklistCompilata.objects.filter(
        compilato_il__date__gte=data_inizio,
    ).exclude(
        esito_obj__codice__in=['ok', 'na']
    ).exclude(
        esito_obj__isnull=True, esito='ok'
    ).exclude(
        esito_obj__isnull=True, esito='na'
    ).select_related(
        'sessione__operatore',
        'checklist_item__postazione_cq',
        'checklist_item__categoria',
        'esito_obj',
    ).prefetch_related('verifiche__verificato_da').order_by('-compilato_il')

    if operatore_id:
        qs = qs.filter(sessione__operatore_id=operatore_id)
    if postazione_id:
        qs = qs.filter(checklist_item__postazione_cq_id=postazione_id)

    problemi = list(qs[:300])

    # KPI
    totale_compilazioni = ChecklistCompilata.objects.filter(
        compilato_il__date__gte=data_inizio
    ).count()
    n_problemi = len(problemi)
    n_verifiche = VerificaChecklist.objects.filter(data_verifica__date__gte=data_inizio).count()
    perc_ok = round((1 - n_problemi / totale_compilazioni) * 100, 1) if totale_compilazioni > 0 else 100

    # Top 10 voci problematiche
    top_voci = {}
    for p in problemi:
        nome = p.checklist_item.nome
        top_voci[nome] = top_voci.get(nome, 0) + 1
    top_voci_list = sorted(top_voci.items(), key=lambda x: -x[1])[:10]

    # Problemi per postazione
    per_postazione = {}
    for p in problemi:
        nome = p.checklist_item.postazione_cq.sigla or p.checklist_item.postazione_cq.nome
        per_postazione[nome] = per_postazione.get(nome, 0) + 1
    per_postazione_list = sorted(per_postazione.items(), key=lambda x: -x[1])

    # Problemi per categoria 5S
    per_categoria = {}
    for p in problemi:
        cat = p.checklist_item.categoria
        nome = cat.nome if cat else 'Altro'
        per_categoria[nome] = per_categoria.get(nome, 0) + 1
    per_categoria_list = sorted(per_categoria.items(), key=lambda x: -x[1])

    # Problemi per operatore
    per_operatore = {}
    for p in problemi:
        op = p.sessione.operatore
        nome = op.get_full_name() or op.username
        per_operatore[nome] = per_operatore.get(nome, 0) + 1
    per_operatore_list = sorted(per_operatore.items(), key=lambda x: -x[1])

    # Confronto inizio vs fine turno
    inizio_count = sum(1 for p in problemi if p.fase == 'inizio')
    fine_count = sum(1 for p in problemi if p.fase == 'fine')

    # Operatori e postazioni per i filtri
    from django.contrib.auth.models import User
    operatori = User.objects.filter(
        groups__name__in=['operatore', 'responsabile', 'titolare']
    ).distinct().order_by('first_name', 'last_name')
    postazioni_filtro = PostazioneCQ.objects.filter(attiva=True).order_by('ordine')

    return render(request, 'turni/storico_checklist.html', {
        'problemi': problemi,
        'periodo': periodo,
        'operatori': operatori,
        'postazioni_filtro': postazioni_filtro,
        'filtro_operatore': operatore_id,
        'filtro_postazione': postazione_id,
        # KPI
        'totale_compilazioni': totale_compilazioni,
        'n_problemi': n_problemi,
        'n_verifiche': n_verifiche,
        'perc_ok': perc_ok,
        # Analytics
        'top_voci_list': top_voci_list,
        'per_postazione_json': json.dumps(per_postazione_list),
        'per_categoria_json': json.dumps(per_categoria_list),
        'per_operatore_list': per_operatore_list,
        'inizio_count': inizio_count,
        'fine_count': fine_count,
    })


@login_required
def api_salva_verifica(request, compilata_id):
    if not utente_nel_gruppo(request.user, 'responsabile', 'titolare'):
        return _json_err('Non autorizzato', 403)
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)

    compilata = get_object_or_404(ChecklistCompilata, pk=compilata_id)
    esito = request.POST.get('esito_verifica', '')
    note = request.POST.get('note', '').strip()

    if esito not in ('confermato', 'non_conforme'):
        return _json_err('Esito non valido.')

    verifica, created = VerificaChecklist.objects.update_or_create(
        compilata=compilata,
        verificato_da=request.user,
        defaults={'esito_verifica': esito, 'note': note},
    )
    return _json_ok({
        'id': verifica.pk,
        'esito': verifica.get_esito_verifica_display(),
        'created': created,
    })


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
