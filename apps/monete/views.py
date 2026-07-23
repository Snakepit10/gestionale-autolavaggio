"""Viste staff del modulo monete: ricariche/regalie e avvio lavaggi.

I webhook dei pagamenti (F4/F5) vivranno anch'essi qui, sotto
/monete/webhook/..., raggiungibili anonimi (il middleware di
autorizzazione blocca solo utenti autenticati non-staff).
"""
import uuid
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.clienti.models import Cliente

from .models import ImpostazioniMonete, MovimentoMoneta, NodoImpianto
from .services import wallet
from .services.lavaggio import avvia_lavaggio


def _staff_required(view_func):
    """Solo staff (stesso pattern di apps/marketing/views.py)."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, 'Non hai i permessi per questa sezione.')
            return redirect('core:home')
        return view_func(request, *args, **kwargs)
    return login_required(_wrapped)


# Tipi di movimento gestibili a mano dall'operatore
_TIPI_ACCREDITO = {'ricarica_cassa', 'regalo', 'promozione', 'rettifica'}


@_staff_required
def movimento_cliente(request, pk):
    """POST dal modal 'Gestisci monete' in storico cliente.

    azione: 'accredita' (ricarica_cassa/regalo/promozione/rettifica)
            oppure 'addebita' (sempre tipo rettifica).
    Per ricarica_cassa e' richiesto l'importo in euro incassato, cosi'
    il movimento resta riconciliabile con la cassa.
    """
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method != 'POST':
        return redirect('clienti:storico-cliente', pk=pk)

    azione = request.POST.get('azione', '')
    tipo = request.POST.get('tipo', '')
    descrizione = (request.POST.get('descrizione') or '').strip()
    try:
        monete = int(request.POST.get('monete', 0))
    except (TypeError, ValueError):
        monete = 0

    if monete < 1:
        messages.error(request, 'Indica un numero di monete valido (>= 1).')
        return redirect('clienti:storico-cliente', pk=pk)

    try:
        if azione == 'accredita':
            if tipo not in _TIPI_ACCREDITO:
                messages.error(request, 'Tipo di accredito non valido.')
                return redirect('clienti:storico-cliente', pk=pk)
            importo = None
            if tipo == 'ricarica_cassa':
                try:
                    importo = round(float(
                        (request.POST.get('importo') or '').replace(',', '.')), 2)
                except (TypeError, ValueError):
                    importo = None
                if not importo or importo <= 0:
                    messages.error(
                        request,
                        'Per la ricarica in cassa indica l\'importo incassato in euro.')
                    return redirect('clienti:storico-cliente', pk=pk)
            wallet.accredita(
                cliente, monete, tipo,
                descrizione or dict(MovimentoMoneta.TIPO_CHOICES)[tipo],
                operatore=request.user, importo=importo,
            )
            messages.success(
                request, f'Accreditate {monete} monete a {cliente.nome_completo} '
                         f'(saldo: {wallet.saldo_di(cliente)}).')
        elif azione == 'addebita':
            wallet.addebita(
                cliente, monete, 'rettifica',
                descrizione or 'Rettifica manuale',
                operatore=request.user,
            )
            messages.success(
                request, f'Rimosse {monete} monete a {cliente.nome_completo} '
                         f'(saldo: {wallet.saldo_di(cliente)}).')
        else:
            messages.error(request, 'Azione non valida.')
    except wallet.SaldoInsufficienteError as exc:
        messages.error(request, str(exc))

    return redirect('clienti:storico-cliente', pk=pk)


@_staff_required
def avvia_staff(request):
    """Avvio lavaggio dal gestionale a scalare dal saldo di un cliente.

    GET: form con ricerca cliente (AJAX clienti:cerca-cliente), nodi
    attivi e impulsi; ?cliente=<pk> preseleziona (bottone da storico).
    POST: esegue l'avvio. La chiave di idempotenza arriva dal form
    (UUID generato al render) cosi' un doppio submit non addebita due
    volte; lo staff puo' forzare il cooldown.
    """
    nodi = NodoImpianto.objects.filter(attivo=True)
    cliente_pre = None
    pre_pk = request.GET.get('cliente') or request.POST.get('cliente_id')
    if pre_pk:
        cliente_pre = Cliente.objects.filter(pk=pre_pk).first()

    if request.method == 'POST':
        nodo = NodoImpianto.objects.filter(
            pk=request.POST.get('nodo_id'), attivo=True).first()
        try:
            impulsi = int(request.POST.get('impulsi', 0))
        except (TypeError, ValueError):
            impulsi = 0
        chiave = (request.POST.get('chiave') or '').strip()[:40]

        if cliente_pre is None:
            messages.error(request, 'Seleziona un cliente.')
        elif nodo is None:
            messages.error(request, 'Seleziona un nodo attivo.')
        else:
            esito = avvia_lavaggio(
                cliente=cliente_pre, nodo=nodo, impulsi=impulsi,
                operatore=request.user,
                chiave_idempotenza=f'staff:{chiave}' if chiave else '',
                forza=request.POST.get('forza') == 'on',
            )
            if esito.ok:
                messages.success(request, esito.messaggio)
            else:
                messages.error(request, esito.messaggio)
            return redirect(
                f"{request.path}?cliente={cliente_pre.pk}")

    return render(request, 'monete/avvia_staff.html', {
        'nodi': nodi,
        'cliente_pre': cliente_pre,
        'saldo_pre': wallet.saldo_di(cliente_pre) if cliente_pre else None,
        'chiave': uuid.uuid4().hex[:32],
        'cfg': ImpostazioniMonete.get_solo(),
    })
