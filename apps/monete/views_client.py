"""Viste area cliente (/app/monete/): saldo, movimenti, avvio lavaggio.

L'acquisto pacchetti online (Stripe/PayPal) si aggancia qui nelle fasi
F4/F5: la pagina 'Le mie monete' mostra i pacchetti solo quando la
vendita online e' attiva nelle impostazioni.
"""
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import ImpostazioniMonete, NodoImpianto, PacchettoMonete
from .services import wallet
from .services.lavaggio import avvia_lavaggio


def _cliente_required(view_func):
    """Login cliente obbligatorio: passa il Cliente alla view."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        cliente = getattr(request.user, 'cliente', None)
        if cliente is None:
            messages.error(request, 'Area riservata ai clienti registrati.')
            return redirect('auth:client-login')
        return view_func(request, cliente, *args, **kwargs)
    return login_required(_wrapped, login_url='/auth/clienti/login/')


def _acquisto_disponibile(cfg) -> bool:
    """True se la vendita online e' attiva e almeno un provider e'
    configurato (flag + credenziali env)."""
    stripe_ok = cfg.stripe_attivo and bool(
        getattr(settings, 'STRIPE_SECRET_KEY', ''))
    paypal_ok = cfg.paypal_attivo and bool(
        getattr(settings, 'PAYPAL_CLIENT_ID', ''))
    return cfg.vendita_online_attiva and (stripe_ok or paypal_ok)


@_cliente_required
def monete_home(request, cliente):
    """'Le mie monete': saldo, movimenti recenti, pacchetti acquistabili."""
    cfg = ImpostazioniMonete.get_solo()
    return render(request, 'clients/monete.html', {
        'cliente': cliente,
        'saldo': wallet.saldo_di(cliente),
        'movimenti': cliente.movimenti_monete.select_related('nodo')[:30],
        'cfg': cfg,
        'acquisto_disponibile': _acquisto_disponibile(cfg),
        'pacchetti': PacchettoMonete.objects.filter(attivo=True),
        'stripe_ok': cfg.stripe_attivo and bool(
            getattr(settings, 'STRIPE_SECRET_KEY', '')),
        'paypal_ok': cfg.paypal_attivo and bool(
            getattr(settings, 'PAYPAL_CLIENT_ID', '')),
    })


@_cliente_required
def lavaggio_scegli(request, cliente):
    """Scelta nodo + numero impulsi (stepper con costo live)."""
    nodi = NodoImpianto.objects.filter(attivo=True)
    return render(request, 'clients/monete_lavaggio.html', {
        'cliente': cliente,
        'saldo': wallet.saldo_di(cliente),
        'nodi': nodi,
        'chiave': uuid.uuid4().hex[:32],
    })


@_cliente_required
def lavaggio_avvia(request, cliente):
    """POST dal form di conferma: spende le monete e invia gli impulsi."""
    if request.method != 'POST':
        return redirect('monete_client:lavaggio')

    nodo = NodoImpianto.objects.filter(
        pk=request.POST.get('nodo_id'), attivo=True).first()
    try:
        impulsi = int(request.POST.get('impulsi', 0))
    except (TypeError, ValueError):
        impulsi = 0
    chiave = (request.POST.get('chiave') or '').strip()[:40]

    if nodo is None:
        messages.error(request, 'Seleziona una postazione valida.')
        return redirect('monete_client:lavaggio')

    esito = avvia_lavaggio(
        cliente=cliente, nodo=nodo, impulsi=impulsi,
        chiave_idempotenza=f'app:{chiave}' if chiave else '',
    )
    return render(request, 'clients/monete_esito.html', {
        'cliente': cliente,
        'esito': esito,
        'nodo': nodo,
        'impulsi': impulsi,
        'saldo': wallet.saldo_di(cliente),
    })
