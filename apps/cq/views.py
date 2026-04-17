import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import DetailView, TemplateView, View

from apps.cq.forms import (
    SchedaCQForm, DifettiFormSet, ModificaPunteggioForm,
    ImpostazionePremioForm, OperatoriTurnoForm,
)
from apps.cq.logic import (
    calcola_indice_mensile, calcola_report_mensile, calcola_saldo_grezzo,
    calcola_turni,
)
from apps.cq.models import (
    SchedaCQ, DifettoCQ, OperatorePostazioneTurno, PunteggioCQ,
    ModificaPunteggio, ImpostazionePremioMensile, Postazione,
    AzioneCorrettiva, CategoriaZona, ZonaConfig, CategoriaDifetto,
    TipoDifettoConfig, ZonaDifettoMapping,
    PostazioneCQ, BloccoPostazione, ConfigurazioneAssegnazione,
    AssegnazionePreset, get_postazione_choices, get_postazioni_ordinate,
    get_postazione_nome, get_postazione_blocco_choices,
)
from apps.cq.permissions import (
    ResponsabileOTitolareMixin, TitolareRequiredMixin,
    QualsivogliaOperatoreMixin, utente_nel_gruppo,
)
from apps.ordini.models import Ordine


# ---------------------------------------------------------------------------
# Helper: postazioni in ordine di lavoro
# ---------------------------------------------------------------------------

def _get_operatori_turno_attivi(post_cq, blocco=None):
    """Trova operatori con sessione turno attiva per questa postazione/blocco."""
    from apps.turni.models import PostazioneTurno
    qs = PostazioneTurno.objects.filter(
        sessione__stato='attivo',
        postazione_cq=post_cq,
    ).select_related('sessione__operatore')
    if blocco:
        qs = qs.filter(blocco=blocco)
    else:
        qs = qs.filter(blocco__isnull=True)
    return list(qs.values_list('sessione__operatore_id', flat=True))


def _build_operatori_turno_forms(request_post=None, ordine=None, scheda=None):
    """
    Costruisce un form OperatoriTurnoForm per ogni postazione/blocco,
    precompilato con: 1) assegnazioni manuali esistenti, oppure
    2) operatori con turno attivo (fallback automatico).
    """
    forms_list = []
    _ordine = scheda.ordine if scheda else ordine
    postazioni_db = PostazioneCQ.objects.filter(attiva=True).prefetch_related('blocchi').order_by('ordine')

    for post_idx, post_cq in enumerate(postazioni_db):
        blocchi = list(post_cq.blocchi.order_by('ordine'))

        if blocchi:
            for blocco in blocchi:
                prefix = f'turno_{post_cq.codice}_{blocco.codice}'
                initial = {'postazione': post_cq.codice, 'blocco_codice': blocco.codice}

                # 1) Assegnazioni manuali esistenti per l'ordine
                existing_ids = []
                if _ordine:
                    existing_ids = list(
                        OperatorePostazioneTurno.objects.filter(
                            ordine=_ordine, postazione=post_cq.codice,
                            blocco_codice=blocco.codice,
                        ).values_list('operatore_id', flat=True)
                    )
                # 2) Fallback: operatori con turno attivo
                if not existing_ids:
                    existing_ids = _get_operatori_turno_attivi(post_cq, blocco)
                if existing_ids:
                    initial['operatori'] = existing_ids

                form = OperatoriTurnoForm(
                    data=request_post or None,
                    initial=initial,
                    prefix=prefix,
                )
                form.postazione_label = f"{post_cq.nome} › {blocco.nome}"
                form.postazione_group = post_cq.nome
                form.postazione_idx = post_idx
                form.color_idx = post_idx % 8
                form.postazione_sigla = post_cq.sigla or post_cq.codice
                form.is_blocco = True
                form.blocco_nome = blocco.nome
                forms_list.append(form)
        else:
            prefix = f'turno_{post_cq.codice}'
            initial = {'postazione': post_cq.codice, 'blocco_codice': ''}

            existing_ids = []
            if _ordine:
                existing_ids = list(
                    OperatorePostazioneTurno.objects.filter(
                        ordine=_ordine, postazione=post_cq.codice,
                        blocco_codice='',
                    ).values_list('operatore_id', flat=True)
                )
            if not existing_ids:
                existing_ids = _get_operatori_turno_attivi(post_cq)
            if existing_ids:
                initial['operatori'] = existing_ids

            form = OperatoriTurnoForm(
                data=request_post or None,
                initial=initial,
                prefix=prefix,
            )
            form.postazione_label = post_cq.nome
            form.postazione_group = post_cq.nome
            form.postazione_idx = post_idx
            form.color_idx = post_idx % 8
            form.postazione_sigla = post_cq.sigla or post_cq.codice
            form.is_blocco = False
            form.blocco_nome = ''
            forms_list.append(form)

    return forms_list


# ---------------------------------------------------------------------------
# Scheda CQ — Crea
# ---------------------------------------------------------------------------

class SchedaCQCreateView(ResponsabileOTitolareMixin, View):
    template_name = 'cq/scheda_cq_form.html'

    def get(self, request, ordine_pk):
        ordine = get_object_or_404(Ordine, pk=ordine_pk)

        # Controlla se esiste già una scheda
        if hasattr(ordine, 'scheda_cq'):
            return redirect('cq:scheda_detail', ordine_pk=ordine_pk)

        scheda_form = SchedaCQForm()
        difetti_formset = DifettiFormSet(prefix='difetti', queryset=DifettoCQ.objects.none())
        turno_forms = _build_operatori_turno_forms(ordine=ordine)

        # Pre-carica segnalazioni difetti degli operatori come difetti esistenti
        from apps.turni.models import SegnalazioneDifetto
        segnalazioni = ordine.segnalazioni_difetti.select_related('operatore', 'postazione_cq').all()
        existing_difetti = []
        for s in segnalazioni:
            azione_map = {'corretto': 'sistemato', 'segnalato': 'cliente_informato'}
            op_nome = s.operatore.get_full_name() or s.operatore.username
            existing_difetti.append({
                'zona': s.zona,
                'tipo_difetto': s.tipo_difetto,
                'gravita': s.gravita,
                'postazione_responsabile': s.postazione_produttore,
                'azione_correttiva': azione_map.get(s.azione, 'sistemato'),
                'note': s.note,
                'descrizione_altro': '',
                'segnalato_da': f"{op_nome} ({s.postazione_cq.sigla or s.postazione_cq.nome})",
            })

        ctx = _choices_context()
        if existing_difetti:
            ctx['existing_difetti_json'] = json.dumps(existing_difetti)

        # Auto-selezione esito basata sulle segnalazioni
        esito_suggerito = ''
        if segnalazioni.exists():
            has_non_corretto = segnalazioni.filter(azione='segnalato').exists()
            if has_non_corretto:
                esito_suggerito = 'non_ok'
            else:
                esito_suggerito = 'ok_con_rilievo'

        return render(request, self.template_name, {
            'ordine': ordine,
            'ordine_items': ordine.items.select_related('servizio_prodotto').all(),
            'scheda_form': scheda_form,
            'difetti_formset': difetti_formset,
            'turno_forms': turno_forms,
            'segnalazioni_operatori': segnalazioni.select_related(
                'operatore', 'postazione_cq'
            ),
            'esito_suggerito': esito_suggerito,
            'mode': 'crea',
            'configurazioni_assegnazione': ConfigurazioneAssegnazione.objects.filter(attiva=True),
            **ctx,
        })

    @transaction.atomic
    def post(self, request, ordine_pk):
        ordine = get_object_or_404(Ordine, pk=ordine_pk)

        if hasattr(ordine, 'scheda_cq'):
            return redirect('cq:scheda_detail', ordine_pk=ordine_pk)

        scheda_form = SchedaCQForm(request.POST)
        difetti_formset = DifettiFormSet(request.POST, request.FILES, prefix='difetti', queryset=DifettoCQ.objects.none())
        turno_forms = _build_operatori_turno_forms(request_post=request.POST)

        esito = request.POST.get('esito', 'ok')
        ha_difetti = esito in ('ok_con_rilievo', 'non_ok')
        difetti_validi = not ha_difetti or difetti_formset.is_valid()

        if scheda_form.is_valid() and difetti_validi:
            scheda = scheda_form.save(commit=False)
            scheda.ordine = ordine
            scheda.compilata_da = request.user
            scheda.rilevato_da = _rilevato_da_from_user(request.user)
            scheda.stato = 'aperta'
            scheda.save()

            _salva_operatori_turno(turno_forms, ordine)

            if ha_difetti and difetti_validi:
                for form in difetti_formset:
                    if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                        difetto = form.save(commit=False)
                        difetto.scheda = scheda
                        difetto.save()

            messages.success(request, 'Scheda CQ salvata correttamente.')
            return redirect('cq:scheda_detail', ordine_pk=ordine_pk)

        return render(request, self.template_name, {
            'ordine': ordine,
            'ordine_items': ordine.items.select_related('servizio_prodotto').all(),
            'scheda_form': scheda_form,
            'difetti_formset': difetti_formset,
            'turno_forms': turno_forms,
            'mode': 'crea',
            'configurazioni_assegnazione': ConfigurazioneAssegnazione.objects.filter(attiva=True),
            **_choices_context(),
        })


# ---------------------------------------------------------------------------
# Scheda CQ — Detail
# ---------------------------------------------------------------------------

class SchedaCQDetailView(QualsivogliaOperatoreMixin, DetailView):
    model = SchedaCQ
    template_name = 'cq/scheda_cq_detail.html'
    context_object_name = 'scheda'

    def get_object(self):
        ordine = get_object_or_404(Ordine, pk=self.kwargs['ordine_pk'])
        return get_object_or_404(SchedaCQ, ordine=ordine)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        scheda = self.object
        ctx['ordine'] = scheda.ordine
        ctx['difetti'] = scheda.difetti.all()
        ctx['punteggi'] = scheda.punteggi.select_related('operatore', 'difetto').all()
        ctx['operatori_turno'] = scheda.ordine.operatori_turno.select_related('operatore').all()
        ctx['is_titolare'] = utente_nel_gruppo(self.request.user, 'titolare')
        # Storico segnalazioni operatori
        from apps.turni.models import SegnalazioneDifetto
        ctx['segnalazioni_operatori'] = SegnalazioneDifetto.objects.filter(
            ordine=scheda.ordine
        ).select_related('operatore', 'postazione_cq')
        return ctx


# ---------------------------------------------------------------------------
# Scheda CQ — Modifica (solo titolare)
# ---------------------------------------------------------------------------

class SchedaCQUpdateView(TitolareRequiredMixin, View):
    template_name = 'cq/scheda_cq_form.html'

    def get(self, request, ordine_pk):
        ordine = get_object_or_404(Ordine, pk=ordine_pk)
        scheda = get_object_or_404(SchedaCQ, ordine=ordine)

        scheda_form = SchedaCQForm(instance=scheda)
        difetti_formset = DifettiFormSet(
            prefix='difetti',
            queryset=DifettoCQ.objects.none(),
        )
        turno_forms = _build_operatori_turno_forms(scheda=scheda)

        existing_difetti = list(scheda.difetti.values(
            'zona', 'tipo_difetto', 'gravita', 'postazione_responsabile',
            'azione_correttiva', 'note', 'descrizione_altro',
        ))

        return render(request, self.template_name, {
            'ordine': ordine,
            'ordine_items': ordine.items.select_related('servizio_prodotto').all(),
            'scheda': scheda,
            'scheda_form': scheda_form,
            'difetti_formset': difetti_formset,
            'turno_forms': turno_forms,
            'mode': 'modifica',
            'configurazioni_assegnazione': ConfigurazioneAssegnazione.objects.filter(attiva=True),
            **_choices_context(),
            'existing_difetti_json': json.dumps(existing_difetti),
        })

    @transaction.atomic
    def post(self, request, ordine_pk):
        ordine = get_object_or_404(Ordine, pk=ordine_pk)
        scheda = get_object_or_404(SchedaCQ, ordine=ordine)

        scheda_form = SchedaCQForm(request.POST, instance=scheda)
        difetti_formset = DifettiFormSet(
            request.POST, request.FILES,
            prefix='difetti',
            queryset=scheda.difetti.all(),
        )
        turno_forms = _build_operatori_turno_forms(request_post=request.POST)

        esito = request.POST.get('esito', scheda.esito)
        ha_difetti = esito in ('ok_con_rilievo', 'non_ok')
        difetti_validi = not ha_difetti or difetti_formset.is_valid()

        if scheda_form.is_valid() and difetti_validi:
            scheda = scheda_form.save()

            # Aggiorna operatori turno
            OperatorePostazioneTurno.objects.filter(ordine=ordine).delete()
            _salva_operatori_turno(turno_forms, ordine)

            # Cancella tutti i difetti esistenti e ricrea da formset
            scheda.difetti.all().delete()
            if ha_difetti and difetti_validi:
                for form in difetti_formset:
                    if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                        difetto = form.save(commit=False)
                        difetto.scheda = scheda
                        difetto.save()

            messages.success(request, 'Scheda CQ aggiornata.')
            return redirect('cq:scheda_detail', ordine_pk=ordine_pk)

        return render(request, self.template_name, {
            'ordine': ordine,
            'scheda': scheda,
            'scheda_form': scheda_form,
            'difetti_formset': difetti_formset,
            'turno_forms': turno_forms,
            'mode': 'modifica',
            **_choices_context(),
        })


# ---------------------------------------------------------------------------
# Dashboard analitica CQ
# ---------------------------------------------------------------------------

class DashboardCQView(ResponsabileOTitolareMixin, TemplateView):
    template_name = 'cq/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        oggi = date.today()

        # Parametri filtro
        periodo = self.request.GET.get('periodo', 'mese')
        if periodo == 'settimana':
            from datetime import timedelta
            data_inizio = oggi - timedelta(days=7)
        elif periodo == 'trimestre':
            from datetime import timedelta
            data_inizio = oggi - timedelta(days=90)
        else:  # mese
            data_inizio = oggi.replace(day=1)

        schede_qs = SchedaCQ.objects.filter(data_ora__date__gte=data_inizio)

        totale = schede_qs.count()
        ok_count = schede_qs.filter(esito='ok').count()
        non_ok_count = schede_qs.filter(esito='non_ok').count()
        perc_ok = round(ok_count / totale * 100, 1) if totale > 0 else 0

        # Zone più problematiche
        zone_problematiche = (
            DifettoCQ.objects
            .filter(scheda__data_ora__date__gte=data_inizio)
            .values('zona')
            .annotate(n=Count('id'))
            .order_by('-n')[:10]
        )
        zona_nome_map = {z.codice: z.nome for z in ZonaConfig.objects.all()}
        zone_labels = [zona_nome_map.get(z['zona'], z['zona']) for z in zone_problematiche]
        zone_counts = [z['n'] for z in zone_problematiche]

        # Gravità distribuzione
        difetti_qs = DifettoCQ.objects.filter(scheda__data_ora__date__gte=data_inizio)
        grav_bassa = difetti_qs.filter(gravita='bassa').count()
        grav_media = difetti_qs.filter(gravita='media').count()
        grav_alta = difetti_qs.filter(gravita='alta').count()

        # Postazioni più penalizzate
        punteggi_neg = (
            PunteggioCQ.objects
            .filter(
                scheda__data_ora__date__gte=data_inizio,
                punti__lt=0,
                difetto__isnull=False,
            )
            .values('difetto__postazione_responsabile')
            .annotate(totale=Count('id'))
            .order_by('-totale')
        )

        # Ultimi difetti
        ultimi_difetti = (
            DifettoCQ.objects
            .filter(scheda__data_ora__date__gte=data_inizio)
            .select_related('scheda', 'scheda__ordine', 'scheda__compilata_da')
            .order_by('-scheda__data_ora')[:20]
        )

        ctx.update({
            'periodo': periodo,
            'data_inizio': data_inizio,
            'totale_schede': totale,
            'ok_count': ok_count,
            'non_ok_count': non_ok_count,
            'perc_ok': perc_ok,
            'zone_labels': zone_labels,
            'zone_counts': zone_counts,
            'grav_bassa': grav_bassa,
            'grav_media': grav_media,
            'grav_alta': grav_alta,
            'punteggi_neg': punteggi_neg,
            'ultimi_difetti': ultimi_difetti,
            'totale_difetti': difetti_qs.count(),
        })
        return ctx


# ---------------------------------------------------------------------------
# Report mensile (solo titolare)
# ---------------------------------------------------------------------------

class ReportMensileView(TitolareRequiredMixin, TemplateView):
    template_name = 'cq/report_mensile.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        oggi = date.today()
        anno = int(self.kwargs.get('anno', oggi.year))
        mese = int(self.kwargs.get('mese', oggi.month))

        report = calcola_report_mensile(anno, mese)

        try:
            impostazione = ImpostazionePremioMensile.objects.get(anno=anno, mese=mese)
        except ImpostazionePremioMensile.DoesNotExist:
            impostazione = None

        modifica_form = ModificaPunteggioForm()
        premio_form = ImpostazionePremioForm(
            initial={'anno': anno, 'mese': mese}
        )

        # Storico punteggi automatici del mese
        punteggi_mese = (
            PunteggioCQ.objects
            .filter(anno=anno, mese=mese)
            .select_related('operatore', 'scheda__ordine', 'difetto')
            .order_by('-data_ora')
        )

        # Modifiche manuali del mese
        modifiche_mese = (
            ModificaPunteggio.objects
            .filter(anno=anno, mese=mese)
            .select_related('operatore', 'creato_da')
            .order_by('-data_ora')
        )

        ctx.update({
            'anno': anno,
            'mese': mese,
            'report': report,
            'impostazione': impostazione,
            'modifica_form': modifica_form,
            'premio_form': premio_form,
            'punteggi_mese': punteggi_mese,
            'modifiche_mese': modifiche_mese,
            'mesi': [
                (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
                (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
                (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
            ],
            'anni': range(2024, oggi.year + 2),
        })
        return ctx


@login_required
def salva_modifica_punteggio(request, anno, mese):
    """AJAX/POST: salva una modifica manuale punteggio (solo titolare)."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return HttpResponseForbidden()

    form = ModificaPunteggioForm(request.POST)
    if form.is_valid():
        modifica = form.save(commit=False)
        modifica.anno = anno
        modifica.mese = mese
        modifica.creato_da = request.user
        modifica.save()
        messages.success(request, 'Modifica punteggio salvata.')
    else:
        messages.error(request, 'Errore nel salvataggio della modifica.')

    return redirect('cq:report_mensile', anno=anno, mese=mese)


@login_required
def salva_impostazione_premio(request):
    """POST: salva o aggiorna il monte premi mensile (solo titolare)."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return HttpResponseForbidden()

    # Pre-carica l'istanza esistente: senza di essa ModelForm fallisce unique_together
    # quando si tenta di aggiornare un monte premi già impostato per quel mese.
    try:
        anno_raw = int(request.POST.get('anno', 0))
        mese_raw = int(request.POST.get('mese', 0))
        instance = ImpostazionePremioMensile.objects.filter(anno=anno_raw, mese=mese_raw).first()
    except (ValueError, TypeError):
        instance = None

    form = ImpostazionePremioForm(request.POST, instance=instance)
    if form.is_valid():
        imp = form.save(commit=False)
        imp.creato_da = request.user
        imp.save()
        messages.success(request, f'Monte premi {imp.mese}/{imp.anno} aggiornato: €{imp.monte_premi}')
        return redirect('cq:report_mensile', anno=imp.anno, mese=imp.mese)

    messages.error(request, f'Errore nel salvataggio del monte premi: {form.errors.as_text()}')
    return redirect('cq:report_mensile', anno=date.today().year, mese=date.today().month)


@login_required
def valida_mese(request, anno, mese):
    """POST: congela il mese (solo titolare)."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return HttpResponseForbidden()

    imp = get_object_or_404(ImpostazionePremioMensile, anno=anno, mese=mese)
    imp.validato = True
    imp.validato_da = request.user
    imp.validato_il = timezone.now()
    imp.save()
    messages.success(request, f'Mese {mese}/{anno} validato e congelato.')
    return redirect('cq:report_mensile', anno=anno, mese=mese)


# ---------------------------------------------------------------------------
# Il mio punteggio (tutti gli operatori autenticati)
# ---------------------------------------------------------------------------

class MioPunteggioView(QualsivogliaOperatoreMixin, TemplateView):
    template_name = 'cq/mio_punteggio.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        operatore = self.request.user
        oggi = date.today()
        anno = int(self.request.GET.get('anno', oggi.year))
        mese = int(self.request.GET.get('mese', oggi.month))

        saldo = calcola_saldo_grezzo(operatore, anno, mese)
        turni = calcola_turni(operatore, anno, mese)
        indice = calcola_indice_mensile(operatore, anno, mese)

        punteggi = (
            PunteggioCQ.objects
            .filter(operatore=operatore, anno=anno, mese=mese)
            .select_related('scheda', 'scheda__ordine', 'difetto')
            .order_by('-data_ora')
        )

        modifiche = ModificaPunteggio.objects.filter(
            operatore=operatore, anno=anno, mese=mese
        ).order_by('-data_ora')

        ctx.update({
            'operatore': operatore,
            'anno': anno,
            'mese': mese,
            'saldo_grezzo': saldo,
            'turni': turni,
            'indice': indice,
            'punteggi': punteggi,
            'modifiche': modifiche,
            'mesi': [
                (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
                (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
                (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
            ],
            'anni': range(2024, oggi.year + 2),
        })
        return ctx


# ---------------------------------------------------------------------------
# Helper interno
# ---------------------------------------------------------------------------

def _rilevato_da_from_user(user):
    """Determina il tipo rilevatore in base al gruppo dell'utente."""
    if utente_nel_gruppo(user, 'titolare'):
        return 'titolare'
    return 'responsabile'


def _choices_context():
    """
    Legge la configurazione CQ dal DB e restituisce il contesto JSON
    necessario al form tablet POS-style.
    """
    # Zone categorie con zone attive
    categorie_qs = CategoriaZona.objects.prefetch_related(
        'zone__difetti_config__tipo_difetto__categoria'
    ).all()

    zone_categorie_list = []   # [(cat_nome, [(codice, nome), ...]), ...]
    zona_produttore = {}       # {codice: postazione_produttore}
    zona_difetti = {}          # {codice: [{cat_nome, tipi: [{codice,nome,richiede_desc}]}]}

    for cat in categorie_qs:
        zone_attive = [z for z in cat.zone.all() if z.attiva]
        if not zone_attive:
            continue
        zone_categorie_list.append((cat.nome, [(z.codice, z.nome) for z in zone_attive]))

        for zona in zone_attive:
            zona_produttore[zona.codice] = zona.postazione_produttore

            # Raggruppa i tipi difetto per categoria
            by_cat = {}
            for mapping in zona.difetti_config.all():
                td = mapping.tipo_difetto
                if not td.attivo:
                    continue
                cat_nome = td.categoria.nome
                by_cat.setdefault(cat_nome, {'cat_nome': cat_nome, 'tipi': []})
                by_cat[cat_nome]['tipi'].append({
                    'codice': td.codice,
                    'nome': td.nome,
                    'richiede_desc': td.richiede_descrizione,
                })
            zona_difetti[zona.codice] = list(by_cat.values())

    return {
        'zone_categorie': zone_categorie_list,
        'post_choices': get_postazione_choices(),
        'azione_choices': AzioneCorrettiva.choices,
        'zona_difetti_json': json.dumps(zona_difetti),
        'zona_produttore_json': json.dumps(zona_produttore),
        'azione_choices_json': json.dumps([[v, l] for v, l in AzioneCorrettiva.choices]),
        'existing_difetti_json': '[]',
    }


def _salva_operatori_turno(turno_forms, ordine):
    """Salva i record OperatorePostazioneTurno per ogni postazione/blocco (supporta più operatori)."""
    for form in turno_forms:
        if form.is_bound and form.is_valid():
            postazione = form.cleaned_data.get('postazione')
            blocco_codice = form.cleaned_data.get('blocco_codice', '')
            operatori = form.cleaned_data.get('operatori') or []
        else:
            postazione = form.initial.get('postazione')
            blocco_codice = form.initial.get('blocco_codice', '')
            operatori = []

        if not postazione:
            continue

        OperatorePostazioneTurno.objects.filter(
            ordine=ordine, postazione=postazione, blocco_codice=blocco_codice
        ).delete()
        for op in operatori:
            OperatorePostazioneTurno.objects.create(
                ordine=ordine, postazione=postazione,
                blocco_codice=blocco_codice, operatore=op,
            )


# ---------------------------------------------------------------------------
# Configurazione CQ (solo titolare)
# ---------------------------------------------------------------------------

class ConfigurazioneCQView(TitolareRequiredMixin, TemplateView):
    template_name = 'cq/configurazione_cq.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['categorie_zona'] = CategoriaZona.objects.prefetch_related('zone').all()
        ctx['categorie_difetto'] = CategoriaDifetto.objects.prefetch_related('tipi').all()
        ctx['zone_config'] = ZonaConfig.objects.select_related('categoria').all()
        ctx['tipi_difetto'] = TipoDifettoConfig.objects.select_related('categoria').all()
        ctx['post_choices'] = get_postazione_choices()
        ctx['post_blocco_choices'] = get_postazione_blocco_choices()
        # Matrice mappings per la tab Mappings
        zone_qs = ZonaConfig.objects.prefetch_related('difetti_config__tipo_difetto').select_related('categoria').all()
        tipi_qs = TipoDifettoConfig.objects.select_related('categoria').filter(attivo=True)
        mapping_set = set(
            ZonaDifettoMapping.objects.values_list('zona_id', 'tipo_difetto_id')
        )
        ctx['zone_matrice'] = zone_qs
        ctx['tipi_matrice'] = tipi_qs
        ctx['mapping_set_json'] = json.dumps(list(mapping_set))
        # Postazioni CQ e Blocchi
        ctx['postazioni_cq'] = PostazioneCQ.objects.prefetch_related('blocchi').select_related('postazione_fisica').all()
        # Postazioni fisiche per il dropdown nella config PostazioneCQ
        from apps.core.models import Postazione as PostazioneFisica
        ctx['postazioni_fisiche'] = PostazioneFisica.objects.filter(attiva=True).order_by('ordine_visualizzazione')
        # Preset assegnazione
        from apps.cq.forms import get_operatori_queryset
        ctx['configurazioni_assegnazione'] = ConfigurazioneAssegnazione.objects.prefetch_related(
            'assegnazioni__operatore'
        ).all()
        ctx['operatori_disponibili'] = get_operatori_queryset()
        return ctx


def _json_ok(data=None, **kwargs):
    return JsonResponse({'ok': True, **(data or {}), **kwargs})


def _json_err(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


@login_required
def api_salva_categoria_zona(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    if not nome:
        return _json_err('Nome obbligatorio')
    if pk:
        obj = get_object_or_404(CategoriaZona, pk=pk)
        obj.nome = nome
        obj.ordine = ordine
        obj.save()
    else:
        obj = CategoriaZona.objects.create(nome=nome, ordine=ordine)
    return _json_ok({'id': obj.pk, 'nome': obj.nome, 'ordine': obj.ordine})


@login_required
def api_elimina_categoria_zona(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    get_object_or_404(CategoriaZona, pk=pk).delete()
    return _json_ok()


@login_required
def api_salva_zona(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    codice = request.POST.get('codice', '').strip()
    cat_id = request.POST.get('categoria_id')
    produttore = request.POST.get('postazione_produttore', '')
    catena_raw = request.POST.getlist('postazioni_catena')
    attiva = request.POST.get('attiva', '1') == '1'
    ordine = int(request.POST.get('ordine', 0))
    if not nome or not codice or not cat_id:
        return _json_err('Nome, codice e categoria obbligatori')
    cat = get_object_or_404(CategoriaZona, pk=cat_id)
    if pk:
        obj = get_object_or_404(ZonaConfig, pk=pk)
        obj.nome = nome
        obj.codice = codice
        obj.categoria = cat
        obj.postazione_produttore = produttore
        obj.postazioni_catena = catena_raw
        obj.attiva = attiva
        obj.ordine = ordine
        obj.save()
    else:
        obj = ZonaConfig.objects.create(
            nome=nome, codice=codice, categoria=cat,
            postazione_produttore=produttore, postazioni_catena=catena_raw,
            attiva=attiva, ordine=ordine,
        )
    return _json_ok({'id': obj.pk, 'nome': obj.nome, 'codice': obj.codice})


@login_required
def api_elimina_zona(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    get_object_or_404(ZonaConfig, pk=pk).delete()
    return _json_ok()


@login_required
def api_salva_categoria_difetto(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    if not nome:
        return _json_err('Nome obbligatorio')
    if pk:
        obj = get_object_or_404(CategoriaDifetto, pk=pk)
        obj.nome = nome
        obj.ordine = ordine
        obj.save()
    else:
        obj = CategoriaDifetto.objects.create(nome=nome, ordine=ordine)
    return _json_ok({'id': obj.pk, 'nome': obj.nome})


@login_required
def api_elimina_categoria_difetto(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    get_object_or_404(CategoriaDifetto, pk=pk).delete()
    return _json_ok()


@login_required
def api_salva_tipo_difetto(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    codice = request.POST.get('codice', '').strip()
    cat_id = request.POST.get('categoria_id')
    richiede_desc = request.POST.get('richiede_descrizione', '0') == '1'
    attivo = request.POST.get('attivo', '1') == '1'
    ordine = int(request.POST.get('ordine', 0))
    if not nome or not codice or not cat_id:
        return _json_err('Nome, codice e categoria obbligatori')
    cat = get_object_or_404(CategoriaDifetto, pk=cat_id)
    if pk:
        obj = get_object_or_404(TipoDifettoConfig, pk=pk)
        obj.nome = nome
        obj.codice = codice
        obj.categoria = cat
        obj.richiede_descrizione = richiede_desc
        obj.attivo = attivo
        obj.ordine = ordine
        obj.save()
    else:
        obj = TipoDifettoConfig.objects.create(
            nome=nome, codice=codice, categoria=cat,
            richiede_descrizione=richiede_desc, attivo=attivo, ordine=ordine,
        )
    return _json_ok({'id': obj.pk, 'nome': obj.nome, 'codice': obj.codice})


@login_required
def api_elimina_tipo_difetto(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    get_object_or_404(TipoDifettoConfig, pk=pk).delete()
    return _json_ok()


@login_required
def api_toggle_mapping(request):
    """Attiva/disattiva un mapping zona ↔ tipo_difetto."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    zona_id = request.POST.get('zona_id')
    tipo_id = request.POST.get('tipo_difetto_id')
    if not zona_id or not tipo_id:
        return _json_err('zona_id e tipo_difetto_id obbligatori')
    zona = get_object_or_404(ZonaConfig, pk=zona_id)
    tipo = get_object_or_404(TipoDifettoConfig, pk=tipo_id)
    obj, created = ZonaDifettoMapping.objects.get_or_create(zona=zona, tipo_difetto=tipo)
    if not created:
        obj.delete()
    return _json_ok({'created': created, 'zona_id': zona.pk, 'tipo_difetto_id': tipo.pk})


# ---------------------------------------------------------------------------
# API: CRUD Postazioni CQ
# ---------------------------------------------------------------------------

@login_required
def api_salva_postazione(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    codice = request.POST.get('codice', '').strip()
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    attiva = request.POST.get('attiva', '1') == '1'
    is_cf = request.POST.get('is_controllo_finale', '0') == '1'
    sigla = request.POST.get('sigla', '').strip()
    pf_id = request.POST.get('postazione_fisica_id') or None
    if not codice or not nome:
        return _json_err('Codice e nome obbligatori')
    if pk:
        obj = get_object_or_404(PostazioneCQ, pk=pk)
        obj.codice = codice
        obj.nome = nome
        obj.sigla = sigla
        obj.ordine = ordine
        obj.attiva = attiva
        obj.is_controllo_finale = is_cf
        obj.postazione_fisica_id = pf_id
        obj.save()
    else:
        obj = PostazioneCQ.objects.create(
            codice=codice, nome=nome, sigla=sigla, ordine=ordine,
            attiva=attiva, is_controllo_finale=is_cf,
            postazione_fisica_id=pf_id,
        )
    return _json_ok({
        'id': obj.pk, 'codice': obj.codice, 'nome': obj.nome,
        'ordine': obj.ordine, 'attiva': obj.attiva,
        'is_controllo_finale': obj.is_controllo_finale,
    })


@login_required
def api_elimina_postazione(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(PostazioneCQ, pk=pk)
    # Controlla se il codice è usato in dati esistenti
    in_uso = OperatorePostazioneTurno.objects.filter(postazione=obj.codice).exists()
    if in_uso:
        return _json_err(
            f'Impossibile eliminare: il codice "{obj.codice}" è usato in schede CQ esistenti.'
        )
    obj.delete()
    return _json_ok()


@login_required
def api_salva_blocco(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    postazione_id = request.POST.get('postazione_id')
    codice = request.POST.get('codice', '').strip()
    nome = request.POST.get('nome', '').strip()
    ordine = int(request.POST.get('ordine', 0))
    if not codice or not nome or not postazione_id:
        return _json_err('Postazione, codice e nome obbligatori')
    postazione = get_object_or_404(PostazioneCQ, pk=postazione_id)
    if pk:
        obj = get_object_or_404(BloccoPostazione, pk=pk)
        obj.postazione = postazione
        obj.codice = codice
        obj.nome = nome
        obj.ordine = ordine
        obj.save()
    else:
        obj = BloccoPostazione.objects.create(
            postazione=postazione, codice=codice, nome=nome, ordine=ordine,
        )
    return _json_ok({
        'id': obj.pk, 'codice': obj.codice, 'nome': obj.nome,
        'ordine': obj.ordine, 'postazione_id': postazione.pk,
        'postazione_nome': postazione.nome,
    })


@login_required
def api_elimina_blocco(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    obj = get_object_or_404(BloccoPostazione, pk=pk)
    in_uso = OperatorePostazioneTurno.objects.filter(blocco_codice=obj.codice).exists()
    if in_uso:
        return _json_err(
            f'Impossibile eliminare: il blocco "{obj.codice}" è usato in schede CQ esistenti.'
        )
    obj.delete()
    return _json_ok()


# ---------------------------------------------------------------------------
# API: CRUD Configurazioni Assegnazione (Preset)
# ---------------------------------------------------------------------------

@login_required
def api_salva_configurazione_assegnazione(request):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    pk = request.POST.get('id')
    nome = request.POST.get('nome', '').strip()
    attiva = request.POST.get('attiva', '1') == '1'
    if not nome:
        return _json_err('Nome obbligatorio')
    if pk:
        obj = get_object_or_404(ConfigurazioneAssegnazione, pk=pk)
        obj.nome = nome
        obj.attiva = attiva
        obj.save()
    else:
        obj = ConfigurazioneAssegnazione.objects.create(
            nome=nome, attiva=attiva, creato_da=request.user,
        )
    return _json_ok({'id': obj.pk, 'nome': obj.nome, 'attiva': obj.attiva})


@login_required
def api_elimina_configurazione_assegnazione(request, pk):
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    get_object_or_404(ConfigurazioneAssegnazione, pk=pk).delete()
    return _json_ok()


@login_required
def api_salva_assegnazioni_preset(request, pk):
    """Bulk save delle righe assegnazione per un preset. Riceve JSON nel body."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    config = get_object_or_404(ConfigurazioneAssegnazione, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_err('JSON non valido')
    assegnazioni = data.get('assegnazioni', [])
    with transaction.atomic():
        config.assegnazioni.all().delete()
        for row in assegnazioni:
            postazione_codice = row.get('postazione_codice', '')
            blocco_codice = row.get('blocco_codice', '')
            operatore_id = row.get('operatore_id')
            if postazione_codice and operatore_id:
                AssegnazionePreset.objects.create(
                    configurazione=config,
                    postazione_codice=postazione_codice,
                    blocco_codice=blocco_codice,
                    operatore_id=operatore_id,
                )
    return _json_ok({'count': config.assegnazioni.count()})


@login_required
def api_get_configurazione_assegnazione(request, pk):
    """Restituisce il dettaglio completo di un preset."""
    if not utente_nel_gruppo(request.user, 'titolare'):
        return _json_err('Non autorizzato', 403)
    config = get_object_or_404(ConfigurazioneAssegnazione, pk=pk)
    assegnazioni = list(
        config.assegnazioni.values(
            'postazione_codice', 'blocco_codice', 'operatore_id'
        )
    )
    return _json_ok({
        'id': config.pk,
        'nome': config.nome,
        'attiva': config.attiva,
        'assegnazioni': assegnazioni,
    })


@login_required
def api_applica_preset(request, pk):
    """Restituisce il mapping del preset per auto-popolare il form CQ."""
    config = get_object_or_404(ConfigurazioneAssegnazione, pk=pk, attiva=True)
    mapping = {}
    for a in config.assegnazioni.all():
        key = a.postazione_codice
        if a.blocco_codice:
            key = f"{a.postazione_codice}_{a.blocco_codice}"
        mapping.setdefault(key, []).append(a.operatore_id)
    return _json_ok({'mapping': mapping})
