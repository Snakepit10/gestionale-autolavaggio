import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models as db_models, transaction
from django.db.models import Count, Sum, Avg, Q, F, Case, When, IntegerField
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
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
    SegnalazioneDifetto, STATI_CHIUSI,
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
    Restituisce gli items in coda per l'operatore della sessione.
    Esclude gli ordini per cui l'operatore ha gia almeno una
    LavorazioneOperatore completata o saltata (sono in 'lavorazioni completate').
    """
    ordini_completati_da_me_ids = list(
        LavorazioneOperatore.objects.filter(
            sessione=sessione, stato__in=STATI_CHIUSI
        ).values_list('ordine_id', flat=True).distinct()
    )

    qs = ItemOrdine.objects.filter(
        stato__in=['in_attesa', 'in_lavorazione'],
        ordine__stato__in=['in_attesa', 'in_lavorazione'],
        servizio_prodotto__tipo='servizio',
    )
    if ordini_completati_da_me_ids:
        qs = qs.exclude(ordine_id__in=ordini_completati_da_me_ids)

    return (
        qs.select_related('ordine', 'ordine__cliente', 'servizio_prodotto')
        .annotate(
            has_priority=Case(
                When(ordine__priorita__gt=0, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by('has_priority', 'ordine__priorita', 'ordine__data_ora')
    )


def _contesto_coda(sessione):
    """Dati della colonna di sinistra della dashboard: coda, lavorazione
    attiva e lavorazioni chiuse in questo turno."""
    ordini_dict = {}
    for item in _get_coda_ordini(sessione):
        ordini_dict.setdefault(item.ordine.pk, {'ordine': item.ordine, 'items': []})['items'].append(item)
    return {
        'ordini_coda': list(ordini_dict.values()),
        'lavorazione_attiva': LavorazioneOperatore.objects.filter(
            sessione=sessione, stato__in=['in_lavorazione', 'in_pausa'],
        ).select_related('ordine', 'postazione_cq', 'blocco').first(),
        'lavorazioni_completate': (
            LavorazioneOperatore.objects.filter(sessione=sessione, stato__in=STATI_CHIUSI)
            .select_related('ordine', 'ordine__cliente', 'postazione_cq', 'blocco')
            .prefetch_related('ordine__items__servizio_prodotto')
            .order_by('-fine')[:20]
        ),
    }


def _postazioni_scelta(sessione):
    """Postazioni fra cui l'operatore puo' scegliere la fase: tutte quelle
    della linea, piu' il controllo finale solo se e' nel suo turno."""
    mie = {pt.postazione_cq_id for pt in sessione.postazioni.all()}
    return [
        p for p in PostazioneCQ.objects.filter(attiva=True).prefetch_related('blocchi').order_by('ordine')
        if not p.is_controllo_finale or p.pk in mie
    ], mie


def _blocchi_fase(post_turno, postazione):
    """Blocchi di una postazione per il turno: quelli scelti a inizio turno,
    altrimenti tutti quelli della postazione (o [None] se non ne ha)."""
    blocchi = [pt.blocco for pt in post_turno if pt.postazione_cq_id == postazione.pk]
    if not blocchi or blocchi == [None]:
        blocchi = list(postazione.blocchi.all()) or [None]
    return blocchi


def _chiudi_ordine_se_finito(ordine):
    """L'ordine e' completato quando TUTTE le PostazioneCQ attive (escluso
    il controllo finale) e tutti i loro blocchi hanno una lavorazione chiusa,
    fatta o saltata. Restituisce True se l'ha chiuso."""
    chiuse = set(LavorazioneOperatore.objects.filter(ordine=ordine, stato__in=STATI_CHIUSI)
                 .values_list('postazione_cq_id', 'blocco_id'))
    postazioni_chiuse = {p for p, _ in chiuse}
    for pcq in PostazioneCQ.objects.filter(attiva=True, is_controllo_finale=False).prefetch_related('blocchi'):
        blocchi = list(pcq.blocchi.all())
        if blocchi:
            if any((pcq.pk, b.pk) not in chiuse for b in blocchi):
                return False
        elif pcq.pk not in postazioni_chiuse:
            return False

    ordine.stato = 'completato'
    ordine.save(update_fields=['stato'])
    now = timezone.now()
    for item in ordine.items.exclude(stato='completato'):
        item.stato = 'completato'
        item.fine_lavorazione = now
        item.save(update_fields=['stato', 'fine_lavorazione'])
    return True


def _fasi_ordine(sessione, ordine):
    """Fasi che l'operatore puo' avviare su un ordine: prima quelle del suo
    turno, poi le altre ancora da fare (chi aspira puo' fare anche plastiche
    e vetri se la macchina davanti non e' pronta).

    stato: 'da_fare', 'in_corso' (ci lavora un altro: chi), 'fatta_da_me'
    (chiusa da lui in questo turno: riavviarla la riapre).
    Le fasi chiuse da altri non si propongono.
    """
    postazioni, mie = _postazioni_scelta(sessione)
    righe = list(LavorazioneOperatore.objects.filter(ordine=ordine).select_related('sessione__operatore'))
    fasi = []
    for p in postazioni:
        sue = [r for r in righe if r.postazione_cq_id == p.pk]
        chiuse = [r for r in sue if r.stato in STATI_CHIUSI]
        blocchi = [b.pk for b in p.blocchi.all()]
        fatta = bool(chiuse) and (not blocchi or {r.blocco_id for r in chiuse} >= set(blocchi))
        aperte_altri = [r for r in sue if r.stato not in STATI_CHIUSI and r.sessione_id != sessione.pk]
        if any(r.stato not in STATI_CHIUSI and r.sessione_id == sessione.pk for r in sue):
            continue  # la sta gia' facendo lui: c'e' il cronometro
        if fatta:
            if not any(r.sessione_id == sessione.pk for r in chiuse):
                continue
            stato = 'fatta_da_me'
        elif aperte_altri:
            stato = 'in_corso'
        else:
            stato = 'da_fare'
        chi = sorted({r.sessione.operatore.first_name or r.sessione.operatore.username for r in aperte_altri})
        fasi.append({
            'id': p.pk, 'nome': p.nome, 'sigla': p.sigla or p.codice[:3].upper(),
            'mia': p.pk in mie, 'stato': stato, 'chi': ', '.join(chi), '_ordine': p.ordine,
        })
    # Prima le sue, poi quelle che vengono dopo la sua nella linea (a chi
    # aspira serve plastiche e vetri, non il prelavaggio), poi le precedenti.
    sua = min((p.ordine for p in postazioni if p.pk in mie), default=0)
    fasi.sort(key=lambda f: (not f['mia'], f['_ordine'] < sua))
    for f in fasi:
        del f['_ordine']
    return fasi


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

    # Carica checklist items per le postazioni del turno, filtrati per fase
    fase_filter = Q(in_apertura=True) if fase == 'inizio' else Q(in_chiusura=True)
    items_qs = ChecklistItem.objects.filter(
        attivo=True, postazione_cq_id__in=post_ids
    ).filter(fase_filter).filter(
        Q(blocco__isnull=True) | Q(blocco_id__in=blocco_ids)
    ).select_related('postazione_cq', 'blocco', 'categoria', 'parent').order_by(
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

    # Costruisci mappa subitems per parent_id (serve anche per POST)
    subitems_map = {}
    for item in items:
        if item.parent_id:
            subitems_map.setdefault(item.parent_id, []).append(item)

    # Insieme di ID parent che hanno subitems (da skippare nel salvataggio)
    parents_con_subitems = set(subitems_map.keys())

    if request.method == 'POST':
        with transaction.atomic():
            for item in items:
                # Skip parent items che fanno solo da contenitore
                if item.pk in parents_con_subitems:
                    continue
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
                # Merce del magazzino assegnata alla postazione: un esito
                # non OK cambia lo stato dell'assegnazione
                if item.assegnazione_id:
                    from apps.magazzino.services import esito_checklist
                    esito_checklist(item, esito_obj, request.user, note)
        if fase == 'inizio':
            messages.success(request, 'Checklist inizio turno compilata.')
            return redirect('turni:dashboard')
        else:
            messages.success(request, 'Checklist fine turno compilata.')
            return redirect('turni:chiudi_turno')

    # Raggruppa solo items di primo livello; attacca i sub-items come attributo
    from collections import OrderedDict
    grouped = OrderedDict()
    for item in items:
        if item.parent_id:
            continue  # skip subitems - verranno mostrati sotto il parent
        item.subitems_list = subitems_map.get(item.pk, [])

        post_key = item.postazione_cq.nome
        if item.blocco:
            post_key = f"{item.postazione_cq.nome} › {item.blocco.nome}"
        if post_key not in grouped:
            color_idx = len(grouped) % 8
            grouped[post_key] = {
                'color_idx': color_idx,
                'sigla': item.postazione_cq.sigla or item.postazione_cq.codice,
                'blocco_nome': item.blocco.nome if item.blocco else '',
                'categorie': OrderedDict(),
            }

        cat = item.categoria
        cat_key = cat.nome if cat else 'Altro'
        if cat_key not in grouped[post_key]['categorie']:
            # Assegna un color_idx basato sull'ordine/pk della categoria (stabile per categoria)
            cat_color = (cat.ordine if cat else 99) % 5 if cat else 4
            grouped[post_key]['categorie'][cat_key] = {
                'nome': cat_key,
                'icona': cat.icona if cat else '',
                'color_idx': cat_color,
                'items': [],
            }
        grouped[post_key]['categorie'][cat_key]['items'].append(item)

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

    contesto_coda = _contesto_coda(sessione)

    # Servizi supplemento per il form aggiungi item (solo servizi marcati is_supplemento)
    supplementi = list(
        ServizioProdotto.objects.filter(attivo=True, is_supplemento=True).values(
            'id', 'titolo', 'prezzo',
        )
    )

    # Zone dove l'operatore e nella CATENA di controllo (non produttore).
    # L'operatore controlla in ingresso: se rileva un difetto lo segnala
    # e il difetto viene attribuito al produttore nella scheda CQ.
    from apps.cq.models import ZonaConfig, ZonaDifettoMapping
    operatore_post_codici = set(pt.postazione_cq.codice for pt in post_turno)
    # Aggiungi anche i codici dei blocchi
    for pt in post_turno:
        if pt.blocco:
            operatore_post_codici.add(pt.blocco.codice)

    # Filtra zone dove l'operatore e in catena (postazioni_catena e un JSONField lista)
    zone_operatore = []
    for zona in ZonaConfig.objects.filter(
        attiva=True
    ).prefetch_related('difetti_config__tipo_difetto').select_related('categoria'):
        catena = zona.postazioni_catena or []
        if any(cod in operatore_post_codici for cod in catena):
            zone_operatore.append(zona)

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
                'produttore': zona.postazione_produttore,
                'categoria': zona.categoria.nome if zona.categoria else '',
                'tipi': tipi,
            })

    return render(request, 'turni/dashboard_operatore.html', {
        **contesto_coda,
        'sessione': sessione,
        'postazioni_turno': post_turno,
        'supplementi_json': json.dumps(supplementi, default=str),
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
        ).exclude(stato__in=STATI_CHIUSI).select_related('postazione_cq', 'blocco')
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
        ordine=ordine, stato__in=STATI_CHIUSI
    ).select_related('postazione_cq', 'blocco', 'sessione__operatore').order_by('inizio'):
        op = lav_c.sessione.operatore
        label = lav_c.postazione_cq.sigla or lav_c.postazione_cq.nome
        if lav_c.blocco:
            label += f" [{lav_c.blocco.nome}]"
        fasi_completate.append({
            'postazione': label,
            'operatore': op.get_full_name() or op.username,
            'tempo_min': round(lav_c.tempo_lavoro_netto_minuti, 1),
            'saltata': lav_c.stato == 'saltato',
        })

    # Stato postazioni
    stati_postazioni = ordine.get_stati_postazioni()

    # Segnalazioni difetti gia fatte per questo ordine
    segnalazioni = list(SegnalazioneDifetto.objects.filter(
        ordine=ordine
    ).values('zona', 'tipo_difetto', 'gravita', 'azione', 'postazione_produttore',
             'note', 'operatore__first_name', 'operatore__username'))

    return _json_ok({
        'ordine': {
            'id': ordine.pk,
            'numero': ordine.numero_progressivo,
            'cliente': cliente_nome,
            'tipo_auto': ordine.tipo_auto or '',
            'nota': ordine.nota or '',
            'stato': ordine.stato,
            'totale': str(ordine.totale_finale),
            'abbonamento': ordine.origine == 'abbonamento' and not ordine.totale_finale,
            'tipo_consegna': ordine.tipo_consegna or '',
            'ora_consegna': ordine.ora_consegna_richiesta.strftime('%H:%M') if ordine.ora_consegna_richiesta else '',
            'items': items,
            'stati_postazioni': stati_postazioni,
        },
        'lavorazione': lavorazione,
        'lavorazioni_attive': lavorazioni_attive,
        'fasi_avviabili': _fasi_ordine(sessione, ordine) if sessione else [],
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

    try:
        scelta = json.loads(request.body or b'{}').get('postazione')
    except (ValueError, AttributeError):
        scelta = None
    if scelta:
        # L'operatore ha scelto la fase: anche una postazione fuori dal suo turno.
        postazioni, _ = _postazioni_scelta(sessione)
        postazione = next((p for p in postazioni if str(p.pk) == str(scelta)), None)
        if not postazione:
            return _json_err('Fase non disponibile.')
        fasi = [(postazione, b) for b in _blocchi_fase(post_turno, postazione)]
    else:
        # Pagine vecchie senza scelta: tutte le postazioni del turno insieme.
        fasi = [(pt.postazione_cq, pt.blocco) for pt in post_turno]

    # Una LavorazioneOperatore per ogni blocco/postazione della fase
    lavorazioni_create = []
    for postazione_cq, blocco in fasi:
        # Evita duplicati: non creare se esiste gia una lavorazione non completata
        existing = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine,
            postazione_cq=postazione_cq, blocco=blocco,
        ).exclude(stato__in=STATI_CHIUSI).first()
        if existing:
            lavorazioni_create.append(existing)
            continue

        # Fase saltata per sbaglio: i tempi partono da adesso.
        saltata = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine,
            postazione_cq=postazione_cq, blocco=blocco, stato='saltato',
        ).first()
        if saltata:
            saltata.stato = 'in_lavorazione'
            saltata.inizio = timezone.now()
            saltata.fine = None
            saltata.tempo_pausa_totale = timedelta()
            saltata.save(update_fields=['stato', 'inizio', 'fine', 'tempo_pausa_totale'])
            lavorazioni_create.append(saltata)
            continue

        # Postazione gia' chiusa da lui su quest'ordine nello stesso turno
        # (un "Fine" premuto troppo presto): riapre quella fase invece di
        # crearne un'altra; il tempo da chiusa conta come pausa.
        chiusa = LavorazioneOperatore.objects.filter(
            sessione=sessione, ordine=ordine,
            postazione_cq=postazione_cq, blocco=blocco, stato='completato',
        ).order_by('-fine').first()
        if chiusa:
            chiusa.riapri()
            lavorazioni_create.append(chiusa)
            continue

        lav = LavorazioneOperatore.objects.create(
            sessione=sessione,
            ordine=ordine,
            postazione_cq=postazione_cq,
            blocco=blocco,
        )
        lavorazioni_create.append(lav)

        # CQ integration: auto-crea OperatorePostazioneTurno per ogni blocco
        OperatorePostazioneTurno.objects.get_or_create(
            ordine=ordine,
            postazione=postazione_cq.codice,
            blocco_codice=blocco.codice if blocco else '',
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
    if lav.stato in STATI_CHIUSI:
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

    _chiudi_ordine_se_finito(ordine)

    return _json_ok({
        'stato': lav.stato,
        'tempo_netto_min': round(lav.tempo_lavoro_netto_minuti, 1),
        'ordine_completato': ordine.stato == 'completato',
    })


@login_required
@transaction.atomic
def api_salta_lavorazione(request, ordine_id):
    """L'operatore non ha segnato i tempi di una fase (dimenticanza o
    altro): la fase scelta conta come fatta, senza tempi. Si possono
    saltare le fasi ancora da fare (non quelle in corso o gia' chiuse)."""
    if request.method != 'POST':
        return _json_err('Metodo non consentito', 405)
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return _json_err('Nessuna sessione turno attiva.')
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    if ordine.stato not in ('in_attesa', 'in_lavorazione'):
        return _json_err("L'ordine non e' piu' in lavorazione.")
    try:
        scelta = json.loads(request.body or b'{}').get('postazione')
    except (ValueError, AttributeError):
        scelta = None
    if not scelta:
        return _json_err('Scegli la fase da saltare.')
    da_fare = {f['id'] for f in _fasi_ordine(sessione, ordine) if f['stato'] == 'da_fare'}
    postazioni, _ = _postazioni_scelta(sessione)
    postazione = next((p for p in postazioni if str(p.pk) == str(scelta) and p.pk in da_fare), None)
    if not postazione:
        return _json_err("Questa fase non si puo' saltare: e' gia' fatta o qualcuno ci sta lavorando.")

    post_turno = list(sessione.postazioni.select_related('postazione_cq', 'blocco').all())
    chiuse = set(LavorazioneOperatore.objects.filter(ordine=ordine, postazione_cq=postazione, stato__in=STATI_CHIUSI)
                 .values_list('blocco_id', flat=True))
    adesso = timezone.now()
    saltate = 0
    for blocco in _blocchi_fase(post_turno, postazione):
        if (blocco.pk if blocco else None) in chiuse:
            continue
        LavorazioneOperatore.objects.create(
            sessione=sessione, ordine=ordine, postazione_cq=postazione, blocco=blocco,
            inizio=adesso, fine=adesso, stato='saltato',
        )
        # La fase l'ha fatta lui: conta per la scheda del controllo qualita'.
        OperatorePostazioneTurno.objects.get_or_create(
            ordine=ordine, postazione=postazione.codice,
            blocco_codice=blocco.codice if blocco else '', operatore=request.user,
        )
        saltate += 1

    if ordine.stato == 'in_attesa':
        ordine.stato = 'in_lavorazione'
        ordine.save(update_fields=['stato'])
    return _json_ok({'saltate': saltate, 'ordine_completato': _chiudi_ordine_se_finito(ordine)})


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
def api_coda_html(request):
    """Colonna coda + completate gia' pronta: la dashboard la ricarica
    ogni pochi secondi per restare aggiornata."""
    sessione = _get_sessione_attiva(request.user)
    if not sessione:
        return HttpResponse(status=204)
    return render(request, 'turni/_coda.html', _contesto_coda(sessione))


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
    zona_codice = data.get('zona', '')
    tipo_difetto = data.get('tipo_difetto', '')
    gravita = data.get('gravita', 'media')
    azione = data.get('azione', 'corretto')
    note = data.get('note', '')

    if not zona_codice or not tipo_difetto:
        return _json_err('Zona e tipo difetto obbligatori.')

    sessione = _get_sessione_attiva(request.user)
    postazione_cq = None
    if sessione:
        pt = sessione.postazioni.select_related('postazione_cq').first()
        if pt:
            postazione_cq = pt.postazione_cq

    if not postazione_cq:
        return _json_err('Nessuna postazione assegnata.')

    # Trova il produttore dalla configurazione zona
    from apps.cq.models import ZonaConfig
    zona_obj = ZonaConfig.objects.filter(codice=zona_codice).first()
    postazione_produttore = zona_obj.postazione_produttore if zona_obj else ''

    segnalazione = SegnalazioneDifetto.objects.create(
        ordine=ordine,
        zona=zona_codice,
        tipo_difetto=tipo_difetto,
        gravita=gravita,
        azione=azione,
        postazione_produttore=postazione_produttore,
        postazione_cq=postazione_cq,
        operatore=request.user,
        note=note,
    )
    return _json_ok({
        'id': segnalazione.pk,
        'zona': segnalazione.zona_nome,
        'tipo_difetto': segnalazione.tipo_difetto_nome,
        'produttore': postazione_produttore,
        'azione': segnalazione.get_azione_display(),
    })


# ---------------------------------------------------------------------------
# Configurazione Checklist (CRUD titolare)
# ---------------------------------------------------------------------------

@login_required
def config_checklist(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        messages.error(request, 'Accesso riservato al titolare.')
        return redirect('core:home')

    # Le voci della merce assegnata alle postazioni si gestiscono dal magazzino
    n_voci_magazzino = ChecklistItem.objects.filter(assegnazione__isnull=False, attivo=True).count()
    all_items = list(ChecklistItem.objects.filter(assegnazione__isnull=True).select_related(
        'postazione_cq', 'blocco', 'categoria', 'parent'
    ).order_by('postazione_cq__ordine', 'blocco__ordine', 'categoria__ordine', 'ordine'))

    # Costruisci mappa subitems per padre
    sub_map = {}
    for it in all_items:
        if it.parent_id:
            sub_map.setdefault(it.parent_id, []).append(it)

    # Solo parent items di primo livello, attacca i subitems
    items = []
    for it in all_items:
        if it.parent_id:
            continue
        it.subitems_list = sub_map.get(it.pk, [])
        items.append(it)

    # Lista parent disponibili per il dropdown (solo top-level)
    parent_items = [it for it in all_items if it.parent_id is None]

    postazioni = PostazioneCQ.objects.filter(attiva=True).prefetch_related('blocchi').order_by('ordine')
    categorie = CategoriaChecklist.objects.prefetch_related('esiti').order_by('ordine')

    return render(request, 'turni/config_checklist.html', {
        'items': items,
        'parent_items': parent_items,
        'postazioni': postazioni,
        'categorie': categorie,
        'n_voci_magazzino': n_voci_magazzino,
    })


@login_required
def api_salva_checklist_item(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)

    pk = request.POST.get('id')
    postazione_cq_id = request.POST.get('postazione_cq_id')
    blocco_id = request.POST.get('blocco_id') or None
    categoria_id = request.POST.get('categoria_id') or None
    parent_id = request.POST.get('parent_id') or None
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    attivo = request.POST.get('attivo', '1') == '1'
    in_apertura = request.POST.get('in_apertura', '1') == '1'
    in_chiusura = request.POST.get('in_chiusura', '1') == '1'

    if not nome or not postazione_cq_id:
        return _json_err('Nome e postazione obbligatori.')
    if not in_apertura and not in_chiusura:
        return _json_err('Seleziona almeno una fase (apertura o chiusura).')

    postazione = get_object_or_404(PostazioneCQ, pk=postazione_cq_id)

    # Verifica parent: non puo essere se stesso e non puo diventare subitem di un subitem
    if parent_id:
        parent = get_object_or_404(ChecklistItem, pk=parent_id)
        if parent.parent_id is not None:
            return _json_err('Un subitem non puo essere padre di un altro subitem.')
        if pk and int(pk) == int(parent_id):
            return _json_err('Un item non puo essere padre di se stesso.')

    if pk:
        obj = get_object_or_404(ChecklistItem, pk=pk)
        if obj.assegnazione_id:
            return _json_err('Voce del magazzino: si gestisce da Magazzino > Assegnazioni.')
        obj.postazione_cq = postazione
        obj.blocco_id = blocco_id if blocco_id else None
        obj.categoria_id = categoria_id if categoria_id else None
        obj.parent_id = parent_id if parent_id else None
        obj.nome = nome
        obj.ordine = ordine
        obj.attivo = attivo
        obj.in_apertura = in_apertura
        obj.in_chiusura = in_chiusura
        obj.save()
    else:
        obj = ChecklistItem.objects.create(
            postazione_cq=postazione,
            blocco_id=blocco_id if blocco_id else None,
            categoria_id=categoria_id if categoria_id else None,
            parent_id=parent_id if parent_id else None,
            nome=nome, ordine=ordine, attivo=attivo,
            in_apertura=in_apertura, in_chiusura=in_chiusura,
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
    if obj.assegnazione_id:
        return _json_err('Voce del magazzino: si gestisce da Magazzino > Assegnazioni.')
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
    from urllib.parse import urlencode

    from django.contrib.auth.models import User
    from apps.turni import report as rep

    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        messages.error(request, 'Accesso riservato.')
        return redirect('core:home')

    chiave, dal, al = rep.periodo(request.GET)
    operatore_id = rep.intero(request.GET.get('operatore'))
    postazione_id = rep.intero(request.GET.get('postazione'))
    dati = rep.report(dal, al, operatore_id, postazione_id)

    filtri = {}
    if chiave == 'personalizzato':
        filtri.update(dal=dal.isoformat(), al=al.isoformat())
    else:
        filtri['periodo'] = chiave
    if operatore_id:
        filtri['operatore'] = operatore_id
    if postazione_id:
        filtri['postazione'] = postazione_id
    operatori = User.objects.filter(
        sessioni_turno__lavorazioni__isnull=False,
    ).distinct().order_by('first_name', 'last_name', 'username')
    return render(request, 'turni/report_lavorazioni.html', {
        **dati,
        'periodi': rep.PERIODI,
        'periodo': chiave,
        'dal': dal,
        'al': al,
        'operatore_id': operatore_id,
        'postazione_id': postazione_id,
        'scelta_operatori': [(u.pk, rep.nome(u)) for u in operatori],
        'scelta_postazioni': PostazioneCQ.objects.order_by('ordine', 'nome'),
        'filtri': urlencode(filtri),
        'fuori_scala_volte': rep.FUORI_SCALA,
        'min_fasi_mediana': rep.MIN_FASI_MEDIANA,
    })


@login_required
def report_ordine_dettaglio(request, ordine_id):
    from apps.turni import report as rep

    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        return redirect('core:home')

    ordine = get_object_or_404(Ordine, pk=ordine_id)
    return render(request, 'turni/report_ordine_dettaglio.html', {
        'ordine': ordine,
        'filtri': request.GET.urlencode(),
        **rep.dettaglio_ordine(ordine),
    })


@login_required
def report_elimina_tempi(request):
    """Elimina le fasi rilevate (non l'ordine) di ordini di prova o sporchi."""
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme
    from apps.turni import report as rep

    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        return redirect('core:home')
    torna = request.POST.get('torna', '')
    if not url_has_allowed_host_and_scheme(torna, {request.get_host()}, request.is_secure()):
        torna = reverse('turni:report_lavorazioni')
    if request.method != 'POST':
        return redirect(torna)

    ordini = [i for i in map(rep.intero, request.POST.getlist('ordine')) if i]
    righe = [i for i in map(rep.intero, request.POST.getlist('lavorazione')) if i]
    if ordini:
        da_eliminare = LavorazioneOperatore.objects.filter(ordine_id__in=ordini)
    elif righe:
        da_eliminare = LavorazioneOperatore.objects.filter(pk__in=righe)
    else:
        messages.warning(request, 'Nessun tempo selezionato.')
        return redirect(torna)

    n_ordini = da_eliminare.values('ordine').distinct().count()
    n_righe, _ = da_eliminare.delete()
    if ordini:
        messages.success(request, f'Eliminati i tempi di {n_ordini} ordin{"e" if n_ordini == 1 else "i"}. Gli ordini restano in cassa.')
    else:
        messages.success(request, 'Fase eliminata.' if n_righe else 'La fase era già stata eliminata.')
    return redirect(torna)


@login_required
def api_report_dati(request):
    if not utente_nel_gruppo(request.user, 'titolare', 'responsabile'):
        return _json_err('Non autorizzato', 403)
    # Placeholder for advanced chart data
    return _json_ok({'message': 'TODO'})
