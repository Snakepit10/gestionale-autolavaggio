"""Contabilita' (Amministrazione): fatture dei fornitori, scadenziario e
piano dei conti. Accesso con la sezione 'contabilita' (Configurazione >
Permessi)."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.magazzino.models import Fornitore

from . import pec
from .fatturapa import LIMITE_FILE
from .importa import Esito, importa_file
from .models import (CENTRI, FatturaPassiva, RegolaFornitore, RigaFatturaPassiva,
                     ScadenzaFattura, VoceConto, ripartizione_valida)

CENTESIMO = Decimal('0.01')


def ha_contabilita(user):
    from apps.auth_system.sezioni import ha_accesso
    return ha_accesso(user, 'contabilita')


def accesso(vista):
    return login_required(user_passes_test(ha_contabilita)(vista))


# ---------------------------------------------------------------- utilita'

def _mese(testo, default=None):
    try:
        return datetime.strptime(testo or '', '%Y-%m').date()
    except ValueError:
        return default


def _data(testo):
    try:
        return datetime.strptime(testo or '', '%Y-%m-%d').date()
    except ValueError:
        return None


def _decimale(testo):
    """'1.234,56' o '1234.56' -> Decimal; vuoto o non valido -> None."""
    t = (testo or '').strip()
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        return Decimal(t) if t else None
    except InvalidOperation:
        return None


def _numero(valore):
    return int(valore) if valore == valore.to_integral_value() else float(valore)


def ripartizione_da_post(post, prefisso):
    """Le quattro percentuali {prefisso}_{centro} -> dict per il JSONField;
    tutte vuote = {} (la ripartizione della voce). ValueError se non valide."""
    valori = {c: post.get(f'{prefisso}_{c}', '').strip() for c, _ in CENTRI}
    valori = {c: v for c, v in valori.items() if v}
    return {c: _numero(p) for c, p in ripartizione_valida(valori).items()}


def campi_ripartizione(rip, predefinita=None):
    """Le quattro caselle della ripartizione per il template."""
    predefinita = predefinita or {}
    return [{'chiave': c, 'nome': n, 'valore': rip.get(c, ''), 'segnaposto': predefinita.get(c, '')}
            for c, n in CENTRI]


def _messaggi_esito(request, esito):
    if esito.nuove:
        messages.success(request, f'Importate {len(esito.nuove)} fatture.' if len(esito.nuove) > 1
                         else 'Importata 1 fattura.')
    if esito.doppie:
        messages.info(request, f'{len(esito.doppie)} gia\' presenti: non importate di nuovo.')
    for errore in esito.errori[:10]:
        messages.error(request, errore)
    if not (esito.nuove or esito.doppie or esito.errori):
        messages.info(request, 'Nessuna fattura trovata.')


def _voci():
    return VoceConto.objects.filter(attiva=True).exclude(sezione='ricavi')


# ---------------------------------------------------------------- fatture

@accesso
def fatture(request):
    if request.method == 'POST':
        azione = request.POST.get('azione')
        if azione == 'carica':
            esito = Esito()
            for f in request.FILES.getlist('file'):
                if f.size > LIMITE_FILE:
                    esito.errori.append(f'{f.name}: file troppo grande')
                    continue
                importa_file(f.name, f.read(), 'upload', esito)
            _messaggi_esito(request, esito)
        elif azione == 'leggi_pec':
            try:
                letti, esito = pec.leggi_casella()
            except pec.ErrorePec as e:
                messages.error(request, str(e))
            else:
                messages.info(request, f'PEC: {letti} messaggi nuovi letti.')
                if letti:
                    _messaggi_esito(request, esito)
        elif azione == 'nuova':
            fattura = _nuova_manuale(request)
            if fattura:
                return redirect('contabilita:fattura', pk=fattura.pk)
        return redirect(request.get_full_path())

    oggi = timezone.localdate()
    mese = _mese(request.GET.get('mese'), oggi.replace(day=1))
    stato = request.GET.get('stato', '')
    qs = FatturaPassiva.objects.select_related('fornitore').prefetch_related('righe__voce')
    if stato == 'da_classificare':
        qs = qs.filter(classificata=False)
    else:
        qs = qs.filter(data__year=mese.year, data__month=mese.month)
    fornitore_id = request.GET.get('fornitore', '')
    if fornitore_id.isdigit():
        qs = qs.filter(fornitore_id=fornitore_id)
    voce_id = request.GET.get('voce', '')
    if voce_id.isdigit():
        qs = qs.filter(righe__voce_id=voce_id).distinct()
    cerca = request.GET.get('q', '').strip()
    if cerca:
        qs = qs.filter(Q(numero__icontains=cerca) | Q(fornitore__ragione_sociale__icontains=cerca)
                       | Q(descrizione__icontains=cerca))
    elenco = list(qs)
    for f in elenco:
        f.voci = sorted({r.voce.nome for r in f.righe.all() if r.voce})

    scadute = (ScadenzaFattura.objects.filter(pagata_il__isnull=True, importo__gt=0, data__lt=oggi)
               .aggregate(s=Sum('importo'))['s'] or Decimal('0'))
    return render(request, 'contabilita/fatture.html', {
        'fatture': elenco,
        'mese': mese,
        'mese_prec': (mese.replace(day=1) - timedelta(days=1)).replace(day=1),
        'mese_succ': (mese.replace(day=28) + timedelta(days=4)).replace(day=1),
        'stato': stato,
        'fornitore_id': fornitore_id,
        'voce_id': voce_id,
        'cerca': cerca,
        'totali': {k: sum((getattr(f, k) for f in elenco), Decimal('0')) for k in ('imponibile', 'iva', 'totale')},
        'da_classificare': FatturaPassiva.objects.filter(classificata=False).count(),
        'scadute': scadute,
        'fornitori': Fornitore.objects.filter(fatture_passive__isnull=False).distinct(),
        'tutti_fornitori': Fornitore.objects.all(),
        'voci': _voci(),
        'pec_configurata': pec.configurata(),
        'ultima_pec': pec.ultima_lettura(),
        'oggi': oggi,
    })


def _nuova_manuale(request):
    p = request.POST
    errori = []
    fornitore = None
    if p.get('fornitore', '').isdigit():
        fornitore = Fornitore.objects.filter(pk=p['fornitore']).first()
    elif p.get('fornitore_nuovo', '').strip():
        fornitore = Fornitore(ragione_sociale=p['fornitore_nuovo'].strip()[:150],
                              partita_iva=p.get('partita_iva', '').strip()[:20])
    else:
        errori.append('scegli il fornitore o scrivi il nome di uno nuovo')
    numero = p.get('numero', '').strip()[:40]
    data = _data(p.get('data'))
    imponibile = _decimale(p.get('imponibile'))
    aliquota = _decimale(p.get('aliquota')) if p.get('aliquota', '').strip() else Decimal('22')
    voce = VoceConto.objects.filter(pk=p.get('voce') or 0).first()
    if not numero:
        errori.append('manca il numero')
    if not data:
        errori.append('manca la data')
    if imponibile is None:
        errori.append('l\'imponibile non e\' un numero')
    if aliquota is None:
        errori.append('l\'aliquota IVA non e\' un numero')
    if errori:
        messages.error(request, 'Fattura non inserita: ' + '; '.join(errori) + '.')
        return None
    with transaction.atomic():
        if not fornitore.pk:
            fornitore.save()
        chiave = f'MAN|{fornitore.pk}|{numero}|{data:%Y-%m-%d}'
        if FatturaPassiva.objects.filter(identificativo=chiave).exists():
            messages.error(request, f'La fattura {numero} di {fornitore} e\' gia\' inserita.')
            return None
        iva = (imponibile * aliquota / 100).quantize(CENTESIMO)
        mese = data.replace(day=1)
        fattura = FatturaPassiva.objects.create(
            fornitore=fornitore, numero=numero, data=data, descrizione=p.get('descrizione', '').strip()[:255],
            imponibile=imponibile, iva=iva, totale=imponibile + iva, competenza_dal=mese,
            competenza_al=mese, origine='manuale', identificativo=chiave)
        RigaFatturaPassiva.objects.create(fattura=fattura, descrizione=fattura.descrizione,
                                          imponibile=imponibile, aliquota_iva=aliquota, voce=voce)
        if imponibile + iva > 0:
            ScadenzaFattura.objects.create(fattura=fattura, data=_data(p.get('scadenza')) or data,
                                           importo=imponibile + iva)
        fattura.aggiorna_classificata()
    messages.success(request, f'Fattura {numero} di {fornitore} inserita.')
    return fattura


@accesso
def fattura(request, pk):
    f = get_object_or_404(FatturaPassiva.objects.select_related('fornitore'), pk=pk)
    if request.GET.get('xml'):
        if not f.file_xml:
            raise Http404
        risposta = HttpResponse(bytes(f.file_xml), content_type='application/xml')
        risposta['Content-Disposition'] = f'attachment; filename="{f.file_nome or "fattura.xml"}"'
        return risposta
    if request.method == 'POST':
        azione = request.POST.get('azione')
        if azione == 'salva':
            _salva_fattura(request, f)
        elif azione in ('pagata', 'non_pagata'):
            scadenza = get_object_or_404(ScadenzaFattura, pk=request.POST.get('scadenza'), fattura=f)
            scadenza.pagata_il = (_data(request.POST.get('data')) or timezone.localdate()) if azione == 'pagata' else None
            scadenza.save(update_fields=['pagata_il'])
        elif azione == 'elimina':
            descrizione = str(f)
            f.delete()
            messages.success(request, f'Eliminata: {descrizione}.')
            return redirect('contabilita:fatture')
        return redirect('contabilita:fattura', pk=f.pk)

    righe = list(f.righe.select_related('voce'))
    for r in righe:
        r.campi_rip = campi_ripartizione(r.ripartizione, r.voce.ripartizione if r.voce else {})
        r.prefisso_rip = f'rip_{r.pk}'
    return render(request, 'contabilita/fattura.html', {
        'f': f,
        'righe': righe,
        'scadenze': f.scadenze.all(),
        'voci': _voci(),
        'centri': CENTRI,
        'regola': RegolaFornitore.objects.filter(fornitore=f.fornitore).select_related('voce').first(),
        'oggi': timezone.localdate(),
    })


def _salva_fattura(request, f):
    p = request.POST
    errori = []
    voci = {str(v.pk): v for v in VoceConto.objects.all()}
    righe = list(f.righe.all())
    for r in righe:
        r.voce = voci.get(p.get(f'voce_{r.pk}', ''))
        try:
            r.ripartizione = ripartizione_da_post(p, f'rip_{r.pk}')
        except ValueError as e:
            errori.append(f'riga {r.numero_linea}: {e}')
    dal, al = _mese(p.get('competenza_dal')), _mese(p.get('competenza_al'))
    if not dal or not al:
        errori.append('i mesi di competenza non sono validi')
    elif al < dal:
        errori.append('il mese finale di competenza e\' prima di quello iniziale')
    if errori:
        messages.error(request, 'Non salvato: ' + '; '.join(errori) + '.')
        return
    with transaction.atomic():
        RigaFatturaPassiva.objects.bulk_update(righe, ['voce', 'ripartizione'])
        f.competenza_dal, f.competenza_al = dal, al
        f.note = p.get('note', '').strip()[:255]
        f.save(update_fields=['competenza_dal', 'competenza_al', 'note'])
        f.aggiorna_classificata()
        if p.get('ricorda'):
            con_voce = [r for r in righe if r.voce]
            if con_voce:
                principale = max(con_voce, key=lambda r: abs(r.imponibile))
                RegolaFornitore.objects.update_or_create(fornitore=f.fornitore, defaults={
                    'voce': principale.voce, 'ripartizione': principale.ripartizione})
                messages.info(request, f'Le prossime fatture di {f.fornitore} andranno su "{principale.voce}".')
    messages.success(request, 'Fattura salvata.')


# ---------------------------------------------------------------- scadenziario

@accesso
def scadenziario(request):
    oggi = timezone.localdate()
    if request.method == 'POST':
        scadenza = get_object_or_404(ScadenzaFattura, pk=request.POST.get('scadenza'))
        scadenza.pagata_il = _data(request.POST.get('data')) or oggi
        scadenza.save(update_fields=['pagata_il'])
        messages.success(request, f'Segnata pagata: {scadenza.fattura}.')
        return redirect('contabilita:scadenziario')
    aperte = list(ScadenzaFattura.objects.filter(pagata_il__isnull=True, importo__gt=0)
                  .select_related('fattura__fornitore')
                  .order_by(F('data').asc(nulls_last=True), 'id'))
    tra_30 = oggi + timedelta(days=30)
    gruppi = [
        ('Scadute', 'danger', [s for s in aperte if s.data and s.data < oggi]),
        ('Entro 30 giorni', 'warning', [s for s in aperte if s.data and oggi <= s.data <= tra_30]),
        ('Oltre 30 giorni o senza data', 'secondary', [s for s in aperte if not s.data or s.data > tra_30]),
    ]
    gruppi = [{'titolo': t, 'colore': c, 'scadenze': l, 'totale': sum((s.importo for s in l), Decimal('0'))}
              for t, c, l in gruppi]
    pagate = (ScadenzaFattura.objects.filter(pagata_il__isnull=False)
              .select_related('fattura__fornitore').order_by('-pagata_il', '-id')[:20])
    return render(request, 'contabilita/scadenziario.html', {
        'gruppi': gruppi,
        'totale': sum((g['totale'] for g in gruppi), Decimal('0')),
        'pagate': pagate,
        'oggi': oggi,
    })


# ---------------------------------------------------------------- piano dei conti

@accesso
def piano_conti(request):
    if request.method == 'POST':
        azione = request.POST.get('azione')
        if azione == 'salva_voce':
            _salva_voce(request)
        elif azione == 'elimina_regola':
            RegolaFornitore.objects.filter(pk=request.POST.get('regola')).delete()
            messages.success(request, 'Regola eliminata.')
        return redirect('contabilita:piano_conti')
    voci = list(VoceConto.objects.all())
    for v in voci:
        v.campi_rip = campi_ripartizione(v.ripartizione)
    sezioni = [{'chiave': k, 'nome': n, 'voci': [v for v in voci if v.sezione == k]}
               for k, n in VoceConto.SEZIONI]
    return render(request, 'contabilita/piano_conti.html', {
        'sezioni': sezioni,
        'campi_nuova': campi_ripartizione({}),
        'sezioni_choices': VoceConto.SEZIONI,
        'flussi': VoceConto.FLUSSI,
        'regole': RegolaFornitore.objects.select_related('fornitore', 'voce'),
    })


def _salva_voce(request):
    p = request.POST
    voce = VoceConto.objects.filter(pk=p.get('id') or 0).first() or VoceConto()
    codice, nome = p.get('codice', '').strip().upper()[:20], p.get('nome', '').strip()[:80]
    errori = []
    if not codice or not nome:
        errori.append('codice e nome sono obbligatori')
    elif VoceConto.objects.filter(codice=codice).exclude(pk=voce.pk).exists():
        errori.append(f'il codice {codice} esiste gia\'')
    if p.get('sezione') not in dict(VoceConto.SEZIONI) or p.get('flusso') not in dict(VoceConto.FLUSSI):
        errori.append('sezione o flusso non validi')
    try:
        ripartizione = ripartizione_da_post(p, 'rip')
    except ValueError as e:
        errori.append(str(e))
    if errori:
        messages.error(request, 'Voce non salvata: ' + '; '.join(errori) + '.')
        return
    voce.codice, voce.nome, voce.sezione, voce.flusso = codice, nome, p['sezione'], p['flusso']
    voce.ripartizione = ripartizione
    voce.attiva = bool(p.get('attiva'))
    voce.ordine = int(p['ordine']) if p.get('ordine', '').isdigit() else voce.ordine
    voce.save()
    messages.success(request, f'Voce "{voce.nome}" salvata.')
