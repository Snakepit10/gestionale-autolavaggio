"""Viste del modulo task operatori.

Accesso: tutti gli utenti "operativi" (is_staff/superuser o gruppi
operatore/responsabile/titolare) — NON il decorator _staff_required di
marketing, perche' gli operatori semplici possono non avere is_staff.
Ogni lettura di Task passa da Task.objects.visibile_a(request.user).
"""
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (CommentoForm, EtichettaForm, ProgettoForm, QuickAddForm,
                    TaskForm)
from .models import Etichetta, Progetto, Task

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


def _notifica_assegnazione(task, nuovi_ids, da_user):
    """Evento WS per il badge navbar. Le task riservate viaggiano con
    titolo generico: il broadcast arriva a tutto lo staff connesso."""
    if not nuovi_ids:
        return
    from apps.api.notify import notify_group
    notify_group('tasks_staff', {
        'type': 'task_assegnata',
        'task_id': task.pk,
        'titolo': task.titolo if task.visibilita == Task.VIS_TUTTI
        else 'Nuova task riservata',
        'assegnatari_ids': list(nuovi_ids),
        'da': da_user.get_full_name() or da_user.username,
    })


def _notifica_completamento(task):
    from apps.api.notify import notify_group
    notify_group('tasks_staff', {
        'type': 'task_completata',
        'task_id': task.pk,
        'assegnatari_ids': list(task.assegnatari.values_list('pk', flat=True)),
    })


def _base_visibili(request):
    return (Task.objects.visibile_a(request.user)
            .select_related('progetto', 'creato_da')
            .prefetch_related('assegnatari', 'etichette', 'sottotask'))


FILTRI_LABEL = {
    'aperte': 'Tutte le aperte',
    'oggi': 'Oggi',
    'prossimi': 'Prossimi 7 giorni',
    'mie': 'Assegnate a me',
    'create': 'Create da me',
    'completate': 'Completate',
}


@_operatore_required
def lista(request):
    """Pagina principale: sidebar filtri/progetti/etichette + lista."""
    filtro = request.GET.get('filtro', 'aperte')
    if filtro not in FILTRI_LABEL:
        filtro = 'aperte'
    oggi = timezone.localtime(timezone.now())

    qs = _base_visibili(request).radice()
    if filtro == 'oggi':
        qs = qs.aperte().filter(scadenza__date__lte=oggi.date())
    elif filtro == 'prossimi':
        qs = qs.aperte().filter(
            scadenza__gte=oggi, scadenza__lte=oggi + timedelta(days=7))
    elif filtro == 'mie':
        qs = qs.aperte().filter(assegnatari=request.user)
    elif filtro == 'create':
        qs = qs.filter(creato_da=request.user, completata=False)
    elif filtro == 'completate':
        qs = qs.filter(completata=True).order_by('-completata_il')[:200]
    else:
        qs = qs.aperte()

    progetto_sel = None
    pid = request.GET.get('progetto', '')
    if pid.isdigit():
        progetto_sel = Progetto.objects.filter(pk=int(pid)).first()
        if progetto_sel and filtro != 'completate':
            qs = qs.filter(progetto=progetto_sel)
        elif progetto_sel:
            qs = [t for t in qs if t.progetto_id == progetto_sel.pk]

    etichetta_sel = None
    eid = request.GET.get('etichetta', '')
    if eid.isdigit():
        etichetta_sel = Etichetta.objects.filter(pk=int(eid)).first()
        if etichetta_sel and not isinstance(qs, list):
            qs = qs.filter(etichette=etichetta_sel)

    q = (request.GET.get('q') or '').strip()
    if q and not isinstance(qs, list):
        qs = qs.filter(titolo__icontains=q)

    # Conteggi sidebar (sempre sulle visibili, non sul filtro corrente)
    visibili_aperte = Task.objects.visibile_a(request.user).aperte().radice()
    conteggi = {
        'oggi': visibili_aperte.filter(scadenza__date__lte=oggi.date()).count(),
        'mie': visibili_aperte.filter(assegnatari=request.user).count(),
        'aperte': visibili_aperte.count(),
    }
    progetti = (Progetto.objects.filter(archiviato=False)
                .annotate(n_aperte=Count('tasks', filter=Q(
                    tasks__completata=False, tasks__parent__isnull=True))))

    return render(request, 'tasks/lista.html', {
        'tasks': qs,
        'filtro': filtro,
        'filtro_label': FILTRI_LABEL[filtro],
        'conteggi': conteggi,
        'progetti': progetti,
        'etichette': Etichetta.objects.all(),
        'progetto_sel': progetto_sel,
        'etichetta_sel': etichetta_sel,
        'q': q,
        'form': TaskForm(),
    })


@_operatore_required
def task_crea(request):
    """POST: quick-add (solo titolo) oppure form completo."""
    if request.method != 'POST':
        return redirect('tasks:lista')

    if request.POST.get('quick') == '1':
        form = QuickAddForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Scrivi un titolo per la task.')
            return redirect(request.POST.get('next') or 'tasks:lista')

        # Aggiunta Rapida stile Todoist: #progetto @etichetta +persona
        # p1..p4 e date in linguaggio naturale dentro il testo.
        from .quick_add import parse_quick
        dati = parse_quick(form.cleaned_data['titolo'].strip(), request.user)

        task = Task.objects.create(
            titolo=dati['titolo'],
            # il #progetto nel testo vince sul progetto della vista corrente
            progetto=dati['progetto'] or form.cleaned_data.get('progetto'),
            priorita=dati['priorita'],
            scadenza=dati['scadenza'],
            creato_da=request.user,
        )
        if dati['etichette']:
            task.etichette.set(dati['etichette'])
        if dati['assegnatari']:
            task.assegnatari.set(dati['assegnatari'])
            _notifica_assegnazione(
                task,
                {u.pk for u in dati['assegnatari']} - {request.user.pk},
                request.user)

        msg = f'Task "{task.titolo}" creata'
        if dati['riconosciuti']:
            msg += ' (' + ', '.join(dati['riconosciuti']) + ')'
        messages.success(request, msg + '.')
        return redirect(request.POST.get('next') or 'tasks:lista')

    form = TaskForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Controlla i campi della task.')
        return redirect('tasks:lista')
    task = form.save(commit=False)
    task.creato_da = request.user
    task.save()
    form.save_m2m()
    _notifica_assegnazione(
        task, set(task.assegnatari.values_list('pk', flat=True)) - {request.user.pk},
        request.user)
    messages.success(request, f'Task "{task.titolo}" creata.')
    return redirect('tasks:task-dettaglio', pk=task.pk)


def _task_visibile_o_404(request, pk):
    return get_object_or_404(
        Task.objects.visibile_a(request.user)
        .select_related('progetto', 'creato_da', 'completata_da', 'parent'),
        pk=pk)


@_operatore_required
def task_dettaglio(request, pk):
    task = _task_visibile_o_404(request, pk)
    return render(request, 'tasks/dettaglio.html', {
        'task': task,
        'form': TaskForm(instance=task),
        'commento_form': CommentoForm(),
        'sottotask': task.sottotask.select_related('completata_da')
                         .prefetch_related('assegnatari'),
        'commenti': task.commenti.select_related('autore'),
        'puo_modificare': task.modificabile_da(request.user),
    })


@_operatore_required
def task_modifica(request, pk):
    task = _task_visibile_o_404(request, pk)
    if request.method != 'POST':
        return redirect('tasks:task-dettaglio', pk=pk)
    if not task.modificabile_da(request.user):
        messages.error(request, 'Solo creatore, assegnatari o titolare '
                                'possono modificare questa task.')
        return redirect('tasks:task-dettaglio', pk=pk)

    prima = set(task.assegnatari.values_list('pk', flat=True))
    form = TaskForm(request.POST, instance=task)
    if not form.is_valid():
        messages.error(request, 'Controlla i campi della task.')
        return redirect('tasks:task-dettaglio', pk=pk)
    form.save()
    dopo = set(task.assegnatari.values_list('pk', flat=True))
    _notifica_assegnazione(task, dopo - prima - {request.user.pk}, request.user)
    messages.success(request, 'Task aggiornata.')
    return redirect('tasks:task-dettaglio', pk=pk)


@_operatore_required
def task_toggle(request, pk):
    """POST AJAX: completa/riapri. Chi vede la task puo' spuntarla."""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    task = _task_visibile_o_404(request, pk)
    if task.completata:
        task.riapri()
    else:
        task.marca_completata(request.user)
        _notifica_completamento(task)
    return JsonResponse({'ok': True, 'completata': task.completata})


@_operatore_required
def task_elimina(request, pk):
    task = _task_visibile_o_404(request, pk)
    if request.method != 'POST':
        return redirect('tasks:task-dettaglio', pk=pk)
    if not task.modificabile_da(request.user):
        messages.error(request, 'Non puoi eliminare questa task.')
        return redirect('tasks:task-dettaglio', pk=pk)
    titolo = task.titolo
    parent_id = task.parent_id
    task.delete()
    messages.success(request, f'Task "{titolo}" eliminata.')
    if parent_id:
        return redirect('tasks:task-dettaglio', pk=parent_id)
    return redirect('tasks:lista')


@_operatore_required
def commento_crea(request, pk):
    task = _task_visibile_o_404(request, pk)
    if request.method != 'POST':
        return redirect('tasks:task-dettaglio', pk=pk)
    form = CommentoForm(request.POST)
    if form.is_valid():
        commento = form.save(commit=False)
        commento.task = task
        commento.autore = request.user
        commento.save()
    return redirect('tasks:task-dettaglio', pk=pk)


@_operatore_required
def sottotask_crea(request, pk):
    """La sotto-task eredita progetto e visibilita' dal parent."""
    task = _task_visibile_o_404(request, pk)
    if request.method != 'POST':
        return redirect('tasks:task-dettaglio', pk=pk)
    titolo = (request.POST.get('titolo') or '').strip()
    if not titolo:
        messages.error(request, 'Scrivi un titolo per la sotto-task.')
        return redirect('tasks:task-dettaglio', pk=pk)
    Task.objects.create(
        titolo=titolo, parent=task, progetto=task.progetto,
        visibilita=task.visibilita, creato_da=request.user,
    )
    return redirect('tasks:task-dettaglio', pk=pk)


# ---------------------------------------------------------------------
# Progetti ed etichette (gestione aperta a tutti gli operatori)
# ---------------------------------------------------------------------

@_operatore_required
def progetti(request):
    if request.method == 'POST':
        form = ProgettoForm(request.POST)
        if form.is_valid():
            p = form.save(commit=False)
            p.creato_da = request.user
            p.save()
            messages.success(request, f'Progetto "{p.nome}" creato.')
        else:
            messages.error(request, 'Nome progetto mancante o gia\' esistente.')
        return redirect('tasks:progetti')
    righe = (Progetto.objects.all()
             .annotate(n_aperte=Count('tasks', filter=Q(tasks__completata=False))))
    return render(request, 'tasks/progetti.html', {
        'righe': righe, 'form': ProgettoForm(),
    })


@_operatore_required
def progetto_modifica(request, pk):
    p = get_object_or_404(Progetto, pk=pk)
    if request.method != 'POST':
        return redirect('tasks:progetti')
    form = ProgettoForm(request.POST, instance=p)
    if form.is_valid():
        form.save()
        messages.success(request, f'Progetto "{p.nome}" aggiornato.')
    else:
        messages.error(request, 'Dati progetto non validi.')
    return redirect('tasks:progetti')


@_operatore_required
def progetto_archivia(request, pk):
    p = get_object_or_404(Progetto, pk=pk)
    if request.method != 'POST':
        return redirect('tasks:progetti')
    p.archiviato = not p.archiviato
    p.save(update_fields=['archiviato'])
    stato = 'archiviato' if p.archiviato else 'ripristinato'
    messages.success(request, f'Progetto "{p.nome}" {stato}.')
    return redirect('tasks:progetti')


@_operatore_required
def etichette(request):
    if request.method == 'POST':
        form = EtichettaForm(request.POST)
        if form.is_valid():
            e = form.save()
            messages.success(request, f'Etichetta "{e.nome}" creata.')
        else:
            messages.error(request, 'Nome etichetta mancante o gia\' esistente.')
        return redirect('tasks:etichette')
    righe = Etichetta.objects.annotate(n_task=Count('tasks'))
    return render(request, 'tasks/etichette.html', {
        'righe': righe, 'form': EtichettaForm(),
    })


@_operatore_required
def etichetta_elimina(request, pk):
    e = get_object_or_404(Etichetta, pk=pk)
    if request.method != 'POST':
        return redirect('tasks:etichette')
    nome = e.nome
    e.delete()
    messages.success(request, f'Etichetta "{nome}" eliminata.')
    return redirect('tasks:etichette')


# ---------------------------------------------------------------------
# API badge navbar
# ---------------------------------------------------------------------

@_operatore_required
def api_conteggio(request):
    """Conteggio task aperte assegnate a me (badge navbar)."""
    mie = Task.objects.aperte().filter(assegnatari=request.user)
    return JsonResponse({
        'aperte': mie.count(),
        'in_ritardo': mie.filter(scadenza__lt=timezone.now()).count(),
    })
