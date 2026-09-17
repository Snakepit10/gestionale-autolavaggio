import json
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ordini.models import Ordine, Pagamento

from .models import Fattura


@login_required
def fatture_home(request):
    """Pagina Fatture: ordini da fatturare raggruppati per cliente
    + elenco fatture emesse."""
    da_fatturare = (
        Ordine.objects
        .filter(richiede_fattura=True, fattura__isnull=True)
        .exclude(stato='annullato')
        .select_related('cliente')
        .prefetch_related('items__servizio_prodotto')
        .order_by('cliente_id', 'data_ora')
    )

    # Raggruppa per cliente mantenendo l'ordine; gli ordini senza
    # cliente finiscono in un gruppo a parte (ragione sociale a mano).
    gruppi = {}
    senza_cliente = []
    for ordine in da_fatturare:
        if ordine.cliente_id:
            gruppi.setdefault(ordine.cliente, []).append(ordine)
        else:
            senza_cliente.append(ordine)
    gruppi_cliente = [
        {'cliente': cliente, 'ordini': ordini,
         'totale': sum((o.totale_finale or Decimal('0') for o in ordini),
                       Decimal('0'))}
        for cliente, ordini in gruppi.items()
    ]

    mostra_archiviate = request.GET.get('archiviate') == '1'
    fatture = (
        Fattura.objects
        .select_related('cliente')
        .prefetch_related('ordini__items__servizio_prodotto')
    )
    if not mostra_archiviate:
        fatture = fatture.exclude(stato='archiviata')

    return render(request, 'fatture/fatture_list.html', {
        'gruppi_cliente': gruppi_cliente,
        'senza_cliente': senza_cliente,
        'totale_senza_cliente': sum(
            (o.totale_finale or Decimal('0') for o in senza_cliente),
            Decimal('0')),
        'fatture': fatture,
        'mostra_archiviate': mostra_archiviate,
        'n_archiviate': Fattura.objects.filter(stato='archiviata').count(),
        'metodi_pagamento': Pagamento.METODO_CHOICES,
        'oggi': timezone.localdate(),
        'numero_suggerito': Fattura.suggerisci_numero(timezone.localdate().year),
    })


@login_required
def suggerisci_numero(request):
    """API: prossimo numero progressivo per l'anno richiesto."""
    try:
        anno = int(request.GET.get('anno', timezone.localdate().year))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Anno non valido'})
    return JsonResponse({'success': True,
                         'numero': Fattura.suggerisci_numero(anno)})


@login_required
@require_POST
def crea_fattura(request):
    """Crea una fattura dagli ordini selezionati (stesso cliente)."""
    try:
        payload = json.loads(request.body)
        ordini_ids = [int(i) for i in payload.get('ordini', [])]
        data_fattura = datetime.strptime(
            payload.get('data', ''), '%Y-%m-%d').date()
        numero = (payload.get('numero') or '').strip()
        ragione_sociale = (payload.get('ragione_sociale') or '').strip()
        conferma_duplicato = bool(payload.get('conferma_duplicato'))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Dati non validi'})

    if not ordini_ids:
        return JsonResponse({'success': False,
                             'error': 'Seleziona almeno un ordine'})
    if not numero:
        return JsonResponse({'success': False,
                             'error': 'Numero fattura obbligatorio'})
    if not ragione_sociale:
        return JsonResponse({'success': False,
                             'error': 'Ragione sociale obbligatoria'})

    ordini = list(
        Ordine.objects
        .filter(pk__in=ordini_ids, richiede_fattura=True,
                fattura__isnull=True)
        .exclude(stato='annullato')
    )
    if len(ordini) != len(set(ordini_ids)):
        return JsonResponse({
            'success': False,
            'error': 'Alcuni ordini non sono piu\' fatturabili: '
                     'ricarica la pagina.'})

    # Una fattura = un cliente: tutti lo stesso, oppure tutti senza.
    clienti_ids = {o.cliente_id for o in ordini}
    if len(clienti_ids) > 1:
        return JsonResponse({
            'success': False,
            'error': 'Gli ordini selezionati appartengono a clienti '
                     'diversi: una fattura vale per un solo cliente.'})

    if (Fattura.objects.filter(numero=numero).exists()
            and not conferma_duplicato):
        return JsonResponse({'success': False, 'warning_duplicato': True,
                             'numero': numero})

    with transaction.atomic():
        fattura = Fattura.objects.create(
            numero=numero,
            data=data_fattura,
            cliente_id=clienti_ids.pop(),
            ragione_sociale=ragione_sociale,
            creata_da=request.user,
        )
        Ordine.objects.filter(pk__in=[o.pk for o in ordini]).update(
            fattura=fattura)
        # Stato iniziale: pagata se ogni ordine e' gia' saldato.
        if all(o.is_pagato for o in ordini):
            fattura.stato = 'pagata'
            fattura.pagata_il = timezone.now()
            fattura.save(update_fields=['stato', 'pagata_il'])

    return JsonResponse({'success': True, 'fattura_id': fattura.pk,
                         'stato': fattura.stato})


@login_required
@require_POST
def segna_pagata(request, pk):
    """Salda la fattura: un Pagamento per ogni ordine non pagato
    (i signal di ordini aggiornano stato_pagamento), poi stato pagata."""
    fattura = get_object_or_404(Fattura, pk=pk)
    if fattura.stato != 'da_pagare':
        return JsonResponse({'success': False,
                             'error': 'La fattura non e\' da pagare.'})
    try:
        payload = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        payload = {}
    metodo = payload.get('metodo', 'bonifico')
    if metodo not in dict(Pagamento.METODO_CHOICES):
        return JsonResponse({'success': False,
                             'error': 'Metodo di pagamento non valido'})

    with transaction.atomic():
        for ordine in fattura.ordini.select_for_update():
            saldo = ordine.saldo_dovuto
            if saldo > 0:
                Pagamento.objects.create(
                    ordine=ordine,
                    importo=saldo,
                    metodo=metodo,
                    riferimento=f'Fattura {fattura.numero}',
                    operatore=request.user,
                )
        fattura.stato = 'pagata'
        fattura.pagata_il = timezone.now()
        fattura.save(update_fields=['stato', 'pagata_il'])

    return JsonResponse({'success': True})


@login_required
@require_POST
def archivia_fattura(request, pk):
    fattura = get_object_or_404(Fattura, pk=pk)
    if fattura.stato != 'pagata':
        return JsonResponse({
            'success': False,
            'error': 'Si puo\' archiviare solo una fattura pagata.'})
    fattura.stato = 'archiviata'
    fattura.archiviata_il = timezone.now()
    fattura.save(update_fields=['stato', 'archiviata_il'])
    return JsonResponse({'success': True})


@login_required
@require_POST
def elimina_fattura(request, pk):
    """Elimina la fattura: gli ordini vengono liberati (SET_NULL) e,
    avendo ancora il flag, tornano tra quelli da fatturare."""
    fattura = get_object_or_404(Fattura, pk=pk)
    if fattura.stato == 'archiviata':
        return JsonResponse({
            'success': False,
            'error': 'Le fatture archiviate non si possono eliminare.'})
    fattura.delete()
    return JsonResponse({'success': True})
