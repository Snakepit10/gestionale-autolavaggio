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

from .models import Fattura, RigaFattura


def _parse_importo(valore):
    """'12.50' -> Decimal; None/'' -> None (= usa il totale ordine)."""
    if valore in (None, ''):
        return None
    importo = Decimal(str(valore))
    if importo < 0:
        raise ValueError('importo negativo')
    return importo.quantize(Decimal('0.01'))


def _parse_righe(raw):
    """Valida le righe manuali [{data, descrizione, importo}, ...]."""
    righe = []
    for r in raw or []:
        descrizione = (r.get('descrizione') or '').strip()[:200]
        if not descrizione:
            raise ValueError('descrizione riga mancante')
        data_riga = datetime.strptime(r.get('data') or '', '%Y-%m-%d').date()
        importo = _parse_importo(r.get('importo'))
        if importo is None:
            raise ValueError('importo riga mancante')
        righe.append({'data': data_riga, 'descrizione': descrizione,
                      'importo': importo})
    return righe


def _dati_da_fatturare():
    """Ordini flaggati e voci manuali in attesa, raggruppati per
    cliente (i senza-cliente a parte). Usato da pagina e stampa."""
    da_fatturare = (
        Ordine.objects
        .filter(richiede_fattura=True, fattura__isnull=True)
        .exclude(stato='annullato')
        .select_related('cliente')
        .prefetch_related('items__servizio_prodotto')
        .order_by('cliente_id', 'data_ora')
    )
    voci_attesa = list(
        RigaFattura.objects.filter(fattura__isnull=True)
        .select_related('cliente').order_by('data', 'id'))

    gruppi = {}
    senza_cliente = []
    for ordine in da_fatturare:
        if ordine.cliente_id:
            gruppi.setdefault(ordine.cliente, []).append(ordine)
        else:
            senza_cliente.append(ordine)
    voci_per_cliente = {}
    voci_senza_cliente = []
    for voce in voci_attesa:
        if voce.cliente_id:
            voci_per_cliente.setdefault(voce.cliente, []).append(voce)
        else:
            voci_senza_cliente.append(voce)

    clienti_gruppi = list(gruppi.keys())
    for cliente in voci_per_cliente:
        if cliente not in gruppi:
            clienti_gruppi.append(cliente)
    gruppi_cliente = []
    for cliente in clienti_gruppi:
        ordini = gruppi.get(cliente, [])
        voci = voci_per_cliente.get(cliente, [])
        gruppi_cliente.append({
            'cliente': cliente, 'ordini': ordini, 'voci': voci,
            'n_elementi': len(ordini) + len(voci),
            'totale': (
                sum((o.totale_finale or Decimal('0') for o in ordini),
                    Decimal('0'))
                + sum((v.importo for v in voci), Decimal('0'))),
        })
    return gruppi_cliente, senza_cliente, voci_senza_cliente


def _gruppo_fatture(cliente, lista):
    return {
        'cliente': cliente,
        'fatture': lista,
        'totale': sum((f.totale for f in lista), Decimal('0')),
        'saldo': sum((f.saldo_dovuto for f in lista), Decimal('0')),
    }


def _raggruppa_fatture(lista):
    per_cliente = {}
    senza = []
    for f in lista:
        if f.cliente_id:
            per_cliente.setdefault(f.cliente, []).append(f)
        else:
            senza.append(f)
    gruppi = [_gruppo_fatture(c, lst) for c, lst in per_cliente.items()]
    gruppi.sort(key=lambda g: g['cliente'].nome_completo.lower())
    if senza:
        gruppi.append(_gruppo_fatture(None, senza))
    return gruppi


def _fatture_emesse():
    return list(
        Fattura.objects
        .select_related('cliente')
        .prefetch_related('ordini__items__servizio_prodotto', 'righe'))


@login_required
def fatture_home(request):
    """Pagina Fatture in tre schede: da fatturare / da pagare / pagate."""
    gruppi_cliente, senza_cliente, voci_senza_cliente = _dati_da_fatturare()
    fatture = _fatture_emesse()

    # Dati per il modal "Modifica fattura" (json_script nel template)
    fatture_json = {
        str(f.pk): {
            'numero': f.numero,
            'data': f.data.isoformat(),
            'ragione_sociale': f.ragione_sociale,
            'stato': f.stato,
            'ordini': [
                {'id': o.pk, 'numero': o.numero_progressivo,
                 'importo': str(o.importo_in_fattura),
                 'totale_finale': str(o.totale_finale or Decimal('0'))}
                for o in f.ordini.all()
            ],
            'righe': [
                {'data': r.data.isoformat(), 'descrizione': r.descrizione,
                 'importo': str(r.importo)}
                for r in f.righe.all()
            ],
        }
        for f in fatture if f.stato != 'archiviata'
    }

    # Tre schede: da fatturare / fatture da pagare / fatture pagate
    # (le archiviate stanno sempre tra le pagate)
    fatture_da_pagare = [f for f in fatture if f.stato == 'da_pagare']
    fatture_pagate = [f for f in fatture
                      if f.stato in ('pagata', 'archiviata')]

    return render(request, 'fatture/fatture_list.html', {
        'fatture_json': fatture_json,
        'gruppi_fatture_da_pagare': _raggruppa_fatture(fatture_da_pagare),
        'gruppi_fatture_pagate': _raggruppa_fatture(fatture_pagate),
        'n_fatture_da_pagare': len(fatture_da_pagare),
        'n_fatture_pagate': len(fatture_pagate),
        'n_da_fatturare': (
            sum(g['n_elementi'] for g in gruppi_cliente)
            + len(senza_cliente) + len(voci_senza_cliente)),
        'gruppi_cliente': gruppi_cliente,
        'senza_cliente': senza_cliente,
        'voci_senza_cliente': voci_senza_cliente,
        'n_senza_cliente': len(senza_cliente) + len(voci_senza_cliente),
        'totale_senza_cliente': (
            sum((o.totale_finale or Decimal('0') for o in senza_cliente),
                Decimal('0'))
            + sum((v.importo for v in voci_senza_cliente), Decimal('0'))),
        'fatture': fatture,
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
def crea_voce_manuale(request):
    """Crea una voce manuale da fatturare (data, descrizione, prezzo).

    NON crea un ordine nel gestionale: e' una voce di comodo per far
    quadrare la fattura. Resta in attesa nel gruppo del cliente
    finche' non viene raggruppata in una fattura.
    """
    from apps.clienti.models import Cliente

    try:
        payload = json.loads(request.body)
        cliente = Cliente.objects.get(pk=int(payload.get('cliente_id')))
        data_voce = datetime.strptime(
            payload.get('data') or '', '%Y-%m-%d').date()
        descrizione = (payload.get('descrizione') or '').strip()[:200]
        importo = _parse_importo(payload.get('importo'))
    except (json.JSONDecodeError, ValueError, TypeError,
            Cliente.DoesNotExist, ArithmeticError):
        return JsonResponse({'success': False, 'error': 'Dati non validi'})

    if not descrizione:
        return JsonResponse({'success': False,
                             'error': 'Descrivi il servizio svolto'})
    if importo is None:
        return JsonResponse({'success': False, 'error': 'Prezzo obbligatorio'})

    voce = RigaFattura.objects.create(
        cliente=cliente, data=data_voce,
        descrizione=descrizione, importo=importo,
        tipo_auto=(payload.get('tipo_auto') or '').strip()[:200],
        targa=(payload.get('targa') or '').strip().upper()[:10],
        matricola=(payload.get('matricola') or '').strip()[:50],
        nota=(payload.get('nota') or '').strip(),
    )
    return JsonResponse({'success': True, 'voce_id': voce.pk})


@login_required
@require_POST
def elimina_voce(request, pk):
    """Elimina una voce manuale ancora in attesa (non in fattura)."""
    voce = get_object_or_404(RigaFattura, pk=pk, fattura__isnull=True)
    voce.delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def modifica_ordine_da_fatturare(request, pk):
    """Corregge i dati di un ordine in attesa di fattura: tipo auto,
    targa, matricola e nota fattura."""
    ordine = Ordine.objects.filter(
        pk=pk, richiede_fattura=True, fattura__isnull=True).first()
    if ordine is None:
        return JsonResponse({
            'success': False,
            'error': 'Ordine non modificabile (gia\' in fattura o '
                     'senza richiesta attiva).'})
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Dati non validi'})

    ordine.tipo_auto = (payload.get('tipo_auto') or '').strip()[:200]
    ordine.fattura_targa = (payload.get('targa') or '').strip().upper()[:10]
    ordine.fattura_matricola = (payload.get('matricola') or '').strip()[:50]
    ordine.fattura_nota = (payload.get('nota') or '').strip()
    ordine.save(update_fields=['tipo_auto', 'fattura_targa',
                               'fattura_matricola', 'fattura_nota'])
    return JsonResponse({'success': True})


@login_required
@require_POST
def crea_fattura(request):
    """Crea una fattura dagli ordini selezionati (stesso cliente)."""
    try:
        payload = json.loads(request.body)
        ordini_ids = [int(i) for i in payload.get('ordini', [])]
        voci_ids = [int(i) for i in payload.get('voci', [])]
        # Importi personalizzati per ordine: {'<id>': '12.50', ...}
        importi = {int(k): _parse_importo(v)
                   for k, v in (payload.get('importi') or {}).items()}
        righe = _parse_righe(payload.get('righe'))
        data_fattura = datetime.strptime(
            payload.get('data', ''), '%Y-%m-%d').date()
        numero = (payload.get('numero') or '').strip()
        ragione_sociale = (payload.get('ragione_sociale') or '').strip()
        conferma_duplicato = bool(payload.get('conferma_duplicato'))
    except (json.JSONDecodeError, ValueError, TypeError,
            ArithmeticError):
        return JsonResponse({'success': False, 'error': 'Dati non validi'})

    if not ordini_ids and not voci_ids:
        return JsonResponse({'success': False,
                             'error': 'Seleziona almeno un elemento'})
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

    voci = list(RigaFattura.objects.filter(
        pk__in=voci_ids, fattura__isnull=True))
    if len(voci) != len(set(voci_ids)):
        return JsonResponse({
            'success': False,
            'error': 'Alcune voci non sono piu\' disponibili: '
                     'ricarica la pagina.'})

    # Una fattura = un cliente: ordini e voci tutti dello stesso
    # cliente, oppure tutti senza.
    clienti_ids = ({o.cliente_id for o in ordini}
                   | {v.cliente_id for v in voci})
    if len(clienti_ids) > 1:
        return JsonResponse({
            'success': False,
            'error': 'Gli elementi selezionati appartengono a clienti '
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
        for ordine in ordini:
            ordine.fattura = fattura
            ordine.fattura_importo = importi.get(ordine.pk)
            ordine.save(update_fields=['fattura', 'fattura_importo'])
        RigaFattura.objects.filter(
            pk__in=[v.pk for v in voci]).update(fattura=fattura)
        for riga in righe:
            RigaFattura.objects.create(
                fattura=fattura, cliente=fattura.cliente, **riga)
        # Stato iniziale: pagata se ogni ordine e' gia' saldato e non
        # ci sono voci/righe manuali da incassare.
        totale_righe = (
            sum((r['importo'] for r in righe), Decimal('0'))
            + sum((v.importo for v in voci), Decimal('0')))
        if (ordini and all(o.is_pagato for o in ordini)
                and totale_righe == 0):
            fattura.stato = 'pagata'
            fattura.pagata_il = timezone.now()
            fattura.save(update_fields=['stato', 'pagata_il'])

    return JsonResponse({'success': True, 'fattura_id': fattura.pk,
                         'stato': fattura.stato})


@login_required
@require_POST
def modifica_fattura(request, pk):
    """Modifica una fattura emessa (non archiviata): testata, importi
    per ordine, righe manuali; gli ordini tolti tornano da fatturare."""
    fattura = get_object_or_404(Fattura, pk=pk)
    if fattura.stato == 'archiviata':
        return JsonResponse({
            'success': False,
            'error': 'Le fatture archiviate non si possono modificare.'})

    try:
        payload = json.loads(request.body)
        numero = (payload.get('numero') or '').strip()
        ragione_sociale = (payload.get('ragione_sociale') or '').strip()
        data_fattura = datetime.strptime(
            payload.get('data', ''), '%Y-%m-%d').date()
        ordini_payload = [
            {'id': int(o.get('id')), 'importo': _parse_importo(o.get('importo'))}
            for o in (payload.get('ordini') or [])
        ]
        righe = _parse_righe(payload.get('righe'))
        conferma_duplicato = bool(payload.get('conferma_duplicato'))
    except (json.JSONDecodeError, ValueError, TypeError,
            ArithmeticError):
        return JsonResponse({'success': False, 'error': 'Dati non validi'})

    if not numero:
        return JsonResponse({'success': False,
                             'error': 'Numero fattura obbligatorio'})
    if not ragione_sociale:
        return JsonResponse({'success': False,
                             'error': 'Ragione sociale obbligatoria'})
    if (numero != fattura.numero
            and Fattura.objects.filter(numero=numero).exclude(pk=pk).exists()
            and not conferma_duplicato):
        return JsonResponse({'success': False, 'warning_duplicato': True,
                             'numero': numero})

    with transaction.atomic():
        attuali = {o.pk: o for o in fattura.ordini.all()}
        tenuti = {o['id']: o for o in ordini_payload if o['id'] in attuali}
        # Ordini tolti dalla fattura: tornano nel pool da fatturare
        # (il flag richiede_fattura resta attivo)
        for pk_ordine, ordine in attuali.items():
            if pk_ordine not in tenuti:
                ordine.fattura = None
                ordine.fattura_importo = None
                ordine.save(update_fields=['fattura', 'fattura_importo'])
            else:
                ordine.fattura_importo = tenuti[pk_ordine]['importo']
                ordine.save(update_fields=['fattura_importo'])
        # Righe manuali: le righe rimaste identiche (data, descrizione,
        # importo) si conservano cosi' non perdono targa/matricola/nota;
        # le altre si sostituiscono.
        esistenti = list(fattura.righe.all())
        for riga in righe:
            match = next(
                (e for e in esistenti
                 if e.data == riga['data']
                 and e.descrizione == riga['descrizione']
                 and e.importo == riga['importo']), None)
            if match is not None:
                esistenti.remove(match)
            else:
                RigaFattura.objects.create(
                    fattura=fattura, cliente=fattura.cliente, **riga)
        for rimossa in esistenti:
            rimossa.delete()

        fattura.numero = numero
        fattura.data = data_fattura
        fattura.ragione_sociale = ragione_sociale
        # Una fattura "pagata" che dopo la modifica contiene ordini non
        # saldati torna da pagare (l'operatore ripassera' da Segna pagata)
        campi = ['numero', 'data', 'ragione_sociale']
        ordini_rimasti = list(fattura.ordini.all())
        if (fattura.stato == 'pagata'
                and any(not o.is_pagato for o in ordini_rimasti)):
            fattura.stato = 'da_pagare'
            fattura.pagata_il = None
            campi += ['stato', 'pagata_il']
        fattura.save(update_fields=campi)

    return JsonResponse({'success': True, 'stato': fattura.stato})


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
    with transaction.atomic():
        # Azzera gli importi personalizzati prima che SET_NULL liberi
        # gli ordini: fuori fattura vale di nuovo il totale reale.
        # Anche le voci manuali tornano in attesa (SET_NULL sulla FK),
        # nel gruppo del loro cliente.
        fattura.ordini.update(fattura_importo=None)
        fattura.delete()
    return JsonResponse({'success': True})


# ======================= STAMPA PDF =======================

_STAMPA_TITOLI = {
    'da-fatturare': 'Ordini da fatturare',
    'da-pagare': 'Fatture da pagare',
    'pagate': 'Fatture pagate',
}


def _pdf_intestazione_gruppo(gruppo, styles):
    from reportlab.platypus import Paragraph
    cliente = gruppo['cliente']
    nome = cliente.nome_completo if cliente else 'Senza cliente'
    extra = []
    if cliente and cliente.telefono:
        extra.append(cliente.telefono)
    if cliente and getattr(cliente, 'partita_iva', ''):
        extra.append(f'P.IVA {cliente.partita_iva}')
    testo = f'<b>{nome}</b>'
    if extra:
        testo += f' <font color="#64748b" size="8">{" - ".join(extra)}</font>'
    return Paragraph(testo, styles['gruppo'])


@login_required
def stampa_pdf(request, sezione):
    """PDF stampabile di una delle tre schede della pagina Fatture."""
    import io

    from django.http import FileResponse, HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    if sezione not in _STAMPA_TITOLI:
        return HttpResponse('Sezione sconosciuta', status=404)

    base = getSampleStyleSheet()
    styles = {
        'titolo': ParagraphStyle('titolo', parent=base['Heading1'],
                                 fontSize=15, spaceAfter=2),
        'sotto': ParagraphStyle('sotto', parent=base['Normal'],
                                fontSize=8, textColor=colors.HexColor('#64748b'),
                                spaceAfter=10),
        'gruppo': ParagraphStyle('gruppo', parent=base['Heading3'],
                                 fontSize=11, spaceBefore=10, spaceAfter=3),
        'cella': ParagraphStyle('cella', parent=base['Normal'], fontSize=8),
    }
    stile_tabella = TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#475569')),
        ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.HexColor('#94a3b8')),
        ('LINEBELOW', (0, 1), (-1, -2), 0.25, colors.HexColor('#e2e8f0')),
        ('LINEABOVE', (0, -1), (-1, -1), 0.75, colors.HexColor('#94a3b8')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ])

    def _p(testo):
        from django.utils.html import escape
        return Paragraph(escape(str(testo or '-')), styles['cella'])

    def _eur(valore):
        return f'{valore:.2f} EUR'.replace('.', ',')

    elementi = [
        Paragraph(_STAMPA_TITOLI[sezione], styles['titolo']),
        Paragraph(
            'Generato il '
            + timezone.localtime().strftime('%d/%m/%Y alle %H:%M'),
            styles['sotto']),
    ]
    totale_generale = Decimal('0')

    if sezione == 'da-fatturare':
        gruppi, senza_cliente, voci_senza = _dati_da_fatturare()
        if senza_cliente or voci_senza:
            gruppi.append({
                'cliente': None, 'ordini': senza_cliente,
                'voci': voci_senza,
                'totale': (
                    sum((o.totale_finale or Decimal('0')
                         for o in senza_cliente), Decimal('0'))
                    + sum((v.importo for v in voci_senza), Decimal('0'))),
            })
        intest = ['Ordine', 'Data', 'Tipo auto', 'Servizi', 'Targa',
                  'Matricola', 'Nota', 'Totale']
        larghezze = [55, 42, 88, 320, 48, 55, 100, 55]
        for gruppo in gruppi:
            elementi.append(_pdf_intestazione_gruppo(gruppo, styles))
            righe = [intest]
            for o in gruppo['ordini']:
                servizi = ', '.join(
                    i.servizio_prodotto.titolo for i in o.items.all()
                ) or (o.nota or '-')
                righe.append([
                    f'#{o.numero_breve}',
                    timezone.localtime(o.data_ora).strftime('%d/%m/%y'),
                    _p(o.tipo_auto), _p(servizi), _p(o.fattura_targa),
                    _p(o.fattura_matricola), _p(o.fattura_nota),
                    _eur(o.totale_finale or Decimal('0')),
                ])
            for v in gruppo['voci']:
                righe.append([
                    'voce', v.data.strftime('%d/%m/%y'),
                    _p(v.tipo_auto), _p(v.descrizione), _p(v.targa),
                    _p(v.matricola), _p(v.nota), _eur(v.importo),
                ])
            righe.append(['', '', '', '', '', '', 'Totale',
                          _eur(gruppo['totale'])])
            totale_generale += gruppo['totale']
            elementi.append(Table(righe, colWidths=larghezze,
                                  style=stile_tabella, repeatRows=1))
    else:
        stati = (['da_pagare'] if sezione == 'da-pagare'
                 else ['pagata', 'archiviata'])
        fatture = [f for f in _fatture_emesse() if f.stato in stati]
        gruppi = _raggruppa_fatture(fatture)
        intest = ['Numero', 'Data', 'Ragione sociale', 'Elementi',
                  'Stato', 'Saldo', 'Totale']
        larghezze = [70, 48, 330, 55, 70, 90, 90]
        for gruppo in gruppi:
            elementi.append(_pdf_intestazione_gruppo(gruppo, styles))
            righe = [intest]
            for f in gruppo['fatture']:
                righe.append([
                    f.numero, f.data.strftime('%d/%m/%y'),
                    _p(f.ragione_sociale),
                    str(f.ordini.count() + f.righe.count()),
                    f.get_stato_display(),
                    _eur(f.saldo_dovuto), _eur(f.totale),
                ])
            righe.append(['', '', '', '', '', 'Totale',
                          _eur(gruppo['totale'])])
            totale_generale += gruppo['totale']
            elementi.append(Table(righe, colWidths=larghezze,
                                  style=stile_tabella, repeatRows=1))

    if len(elementi) == 2:
        elementi.append(Paragraph('Nessun elemento.', styles['cella']))
    else:
        elementi.append(Spacer(1, 8 * mm))
        elementi.append(Paragraph(
            f'<b>Totale complessivo: {_eur(totale_generale)}</b>',
            styles['gruppo']))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=_STAMPA_TITOLI[sezione])
    doc.build(elementi)
    buffer.seek(0)
    nome_file = (f'{sezione}-'
                 f'{timezone.localdate().strftime("%Y%m%d")}.pdf')
    return FileResponse(buffer, filename=nome_file,
                        content_type='application/pdf')
