"""Lato cliente / frontend pubblico.

- Landing pubblica
- Registrazione
- Dashboard cliente
- Flusso prenotazione (catalogo + slot picker + conferma)
"""
import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.clienti.models import Cliente
from apps.core.models import ServizioProdotto, Categoria
from apps.prenotazioni.models import (
    Prenotazione, SlotPrenotazione, ConfigurazioneSlot,
)

from .forms import RegistrazioneClienteForm


def _is_cliente(user):
    return user.is_authenticated and hasattr(user, 'cliente')


def _is_staff_app(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def root_redirect(request):
    """Smart routing su /:
    - Staff/admin -> dashboard staff (core:home)
    - Cliente loggato -> dashboard cliente
    - Anonimo -> landing pubblica
    """
    if _is_staff_app(request.user):
        return redirect('core:home')
    if _is_cliente(request.user):
        return redirect('clients:dashboard')
    return redirect('clients:landing')


def landing(request):
    """Landing page pubblica con CTA login/registrazione/prenotazione."""
    # Servizi vetrina (top 6)
    servizi = (
        ServizioProdotto.objects
        .filter(attivo=True, tipo='servizio', is_supplemento=False)
        .select_related('categoria')
        .order_by('categoria__ordine', 'titolo')[:6]
    )
    return render(request, 'clients/landing.html', {
        'servizi': servizi,
    })


def register(request):
    """Registrazione cliente privato."""
    if request.user.is_authenticated:
        return redirect('clients:dashboard')

    if request.method == 'POST':
        form = RegistrazioneClienteForm(request.POST)
        if form.is_valid():
            user, cliente = form.save()
            login(request, user)
            messages.success(request, f"Benvenuto {cliente.nome}! Account creato.")
            return redirect('clients:dashboard')
    else:
        form = RegistrazioneClienteForm()

    return render(request, 'clients/register.html', {'form': form})


@login_required
def dashboard(request):
    """Area cliente: prossime prenotazioni, ultimi ordini, punti fedelta."""
    if _is_staff_app(request.user) and not _is_cliente(request.user):
        return redirect('core:home')

    cliente = getattr(request.user, 'cliente', None)
    if not cliente:
        messages.warning(request, "Account senza profilo cliente. Contatta l'autolavaggio.")
        return redirect('clients:landing')

    oggi = timezone.now().date()
    prenotazioni_prossime = (
        Prenotazione.objects.filter(
            cliente=cliente,
            slot__data__gte=oggi,
            stato__in=['confermata', 'in_attesa'],
        )
        .select_related('slot')
        .prefetch_related('servizi')
        .order_by('slot__data', 'slot__ora_inizio')[:5]
    )
    prenotazioni_passate = (
        Prenotazione.objects.filter(cliente=cliente, slot__data__lt=oggi)
        .select_related('slot')
        .prefetch_related('servizi')
        .order_by('-slot__data')[:5]
    )

    # Punti fedelta (se esiste il modello)
    punti_totali = 0
    try:
        from apps.clienti.models import PuntiFedelta
        pf = PuntiFedelta.objects.filter(cliente=cliente).first()
        if pf:
            punti_totali = pf.punti_disponibili if hasattr(pf, 'punti_disponibili') else 0
    except Exception:
        pass

    return render(request, 'clients/dashboard.html', {
        'cliente': cliente,
        'prenotazioni_prossime': prenotazioni_prossime,
        'prenotazioni_passate': prenotazioni_passate,
        'punti_totali': punti_totali,
    })


def booking(request):
    """Catalogo + form prenotazione cliente.

    Anonimi possono vedere il catalogo, ma per prenotare serve login.
    """
    categorie_pub = (
        Categoria.objects.filter(attiva=True, mostra_catalogo=True)
        if hasattr(Categoria, 'mostra_catalogo')
        else Categoria.objects.filter(attiva=True)
    )
    servizi = (
        ServizioProdotto.objects
        .filter(attivo=True, tipo='servizio', is_supplemento=False)
        .select_related('categoria')
        .order_by('categoria__ordine', 'titolo')
    )
    return render(request, 'clients/booking.html', {
        'categorie': categorie_pub,
        'servizi': servizi,
    })


@login_required
def slot_disponibili_pub(request):
    """API JSON: slot disponibili per data (riusa logica esistente)."""
    data_str = request.GET.get('data')
    if not data_str:
        return JsonResponse({'error': 'Parametro data mancante'}, status=400)
    try:
        data_richiesta = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data non valida'}, status=400)

    # Genera slot da configurazione se non esistono
    giorno_settimana = data_richiesta.weekday()
    for config in ConfigurazioneSlot.objects.filter(
        giorno_settimana=giorno_settimana, attivo=True
    ):
        config.genera_slot_per_data(data_richiesta)

    now = timezone.localtime(timezone.now())
    is_oggi = data_richiesta == now.date()

    slot_qs = SlotPrenotazione.objects.filter(
        data=data_richiesta, disponibile=True
    ).order_by('ora_inizio')
    out = []
    for s in slot_qs:
        posti_liberi = max(0, s.max_prenotazioni - s.prenotazioni_attuali)
        is_past = is_oggi and s.ora_inizio < now.time()
        if is_past or posti_liberi <= 0:
            continue  # Per il cliente mostra solo slot prenotabili
        out.append({
            'id': s.id,
            'ora_inizio': s.ora_inizio.strftime('%H:%M'),
            'ora_fine': s.ora_fine.strftime('%H:%M'),
            'posti_liberi': posti_liberi,
        })
    return JsonResponse({'slot': out})


@login_required
@require_POST
def crea_prenotazione_pub(request):
    """Crea prenotazione cliente.

    Body JSON: {data, ora, servizi: [id, id], tipo_auto, nota}
    """
    if not _is_cliente(request.user):
        return JsonResponse({'error': 'Devi essere un cliente per prenotare'}, status=403)

    cliente = request.user.cliente

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON non valido'}, status=400)

    data_str = body.get('data')
    ora_str = body.get('ora')
    servizi_ids = body.get('servizi') or []
    tipo_auto = (body.get('tipo_auto') or '').strip()
    nota = (body.get('nota') or '').strip()

    if not data_str or not ora_str:
        return JsonResponse({'error': 'Data e ora obbligatorie'}, status=400)
    if not servizi_ids:
        return JsonResponse({'error': 'Seleziona almeno un servizio'}, status=400)

    try:
        data_p = datetime.strptime(data_str, '%Y-%m-%d').date()
        ora_inizio = datetime.strptime(ora_str, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Formato data/ora non valido'}, status=400)

    servizi = list(
        ServizioProdotto.objects.filter(
            id__in=servizi_ids, attivo=True, tipo='servizio'
        )
    )
    if not servizi:
        return JsonResponse({'error': 'Servizi non trovati'}, status=400)
    durata = sum(s.durata_minuti or 30 for s in servizi)

    # get_or_create slot
    ora_fine_dt = datetime.combine(data_p, ora_inizio) + timedelta(minutes=durata)
    slot, _ = SlotPrenotazione.objects.get_or_create(
        data=data_p, ora_inizio=ora_inizio,
        defaults={
            'ora_fine': ora_fine_dt.time(),
            'max_prenotazioni': 1,
            'prenotazioni_attuali': 0,
            'disponibile': True,
        },
    )
    if slot.posti_disponibili <= 0:
        return JsonResponse({'error': 'Slot non piu disponibile'}, status=400)

    prenotazione = Prenotazione.objects.create(
        cliente=cliente,
        slot=slot,
        durata_stimata_minuti=durata,
        stato='confermata',
        tipo_auto=tipo_auto,
        nota_cliente=nota,
    )
    prenotazione.servizi.set(servizi)

    return JsonResponse({
        'ok': True,
        'codice': prenotazione.codice_prenotazione,
        'data': data_p.strftime('%d/%m/%Y'),
        'ora': ora_inizio.strftime('%H:%M'),
        'redirect': reverse('clients:dashboard'),
    })


@login_required
def annulla_prenotazione(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    if not _is_cliente(request.user):
        return JsonResponse({'error': 'Non autorizzato'}, status=403)
    try:
        p = Prenotazione.objects.get(pk=pk, cliente=request.user.cliente)
    except Prenotazione.DoesNotExist:
        return JsonResponse({'error': 'Prenotazione non trovata'}, status=404)
    if not p.can_be_cancelled:
        return JsonResponse({'error': 'Non e possibile annullare questa prenotazione'}, status=400)
    if hasattr(p, 'annulla'):
        p.annulla('Annullata dal cliente')
    else:
        p.stato = 'annullata'
        p.save(update_fields=['stato'])
    return JsonResponse({'ok': True})
