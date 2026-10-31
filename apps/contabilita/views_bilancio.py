"""Conto economico, flussi di cassa e cespiti (Amministrazione >
Contabilita')."""
import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import conto_economico as ce_srv
from .models import CENTRI, Cespite, MovimentoFinanziario, RigaFatturaPassiva, VoceConto
from .views import _data, _decimale, accesso


def _anno(request):
    try:
        return int(request.GET.get('anno', ''))
    except ValueError:
        return timezone.localdate().year


def _gruppi(vista):
    if vista == 'trimestri':
        return [(f'T{t + 1}', [3 * t, 3 * t + 1, 3 * t + 2]) for t in range(4)]
    return [(nome, [i]) for i, nome in enumerate(ce_srv.NOMI_MESI)]


def _link(chiave, anno):
    """Dove porta il clic su una riga."""
    from django.urls import reverse
    if chiave.startswith('voce:') or chiave == 'dacl':
        return f"{reverse('contabilita:dettaglio')}?chiave={chiave}&anno={anno}"
    if chiave == 'personale':
        return f"{reverse('personale:anno')}?anno={anno}"
    if chiave == 'ammortamenti':
        return reverse('contabilita:cespiti')
    if chiave.startswith('ricavi:'):
        return f"{reverse('finanze:report_periodo')}?data_inizio={anno}-01-01&data_fine={anno}-12-31"
    return ''


def _serie_json(serie):
    return json.dumps({k: [float(x) for x in v] for k, v in serie.items()})


@accesso
def conto_economico(request):
    anno = _anno(request)
    vista = request.GET.get('vista', 'mesi')
    mesi = [date(anno, m, 1) for m in range(1, 13)]
    ce = ce_srv.conto_economico(mesi)
    gruppi = _gruppi(vista)
    righe = ce_srv.raggruppa(ce, gruppi)
    precedente = {}
    if request.GET.get('confronto'):
        prec = ce_srv.conto_economico([date(anno - 1, m, 1) for m in range(1, 13)])
        precedente = {r['chiave']: r['totale'] for r in prec['righe'] if r['chiave']}
    for r in righe:
        r['link'] = _link(r['chiave'], anno)
        if precedente and r['chiave']:
            r['prec'] = precedente.get(r['chiave'], Decimal('0'))
            r['var'] = ((r['totale'] - r['prec']) / abs(r['prec']) * 100) if r['prec'] else None
    kpi = ce['kpi']
    return render(request, 'contabilita/conto_economico.html', {
        'anno': anno,
        'vista': vista,
        'colonne': [g[0] for g in gruppi],
        'righe': righe,
        'confronto': bool(precedente),
        'kpi': kpi,
        'pct': {k: (v / kpi['ricavi'] * 100 if kpi['ricavi'] else None) for k, v in kpi.items()},
        'controlli': ce_srv.controlli(mesi, ce),
        'serie_json': _serie_json(ce['serie']),
        'etichette_json': json.dumps(ce_srv.NOMI_MESI),
    })


@accesso
def flussi_di_cassa(request):
    anno = _anno(request)
    vista = request.GET.get('vista', 'mesi')
    mesi = [date(anno, m, 1) for m in range(1, 13)]
    fc = ce_srv.flussi_di_cassa(mesi)
    gruppi = _gruppi(vista)
    righe = ce_srv.raggruppa(fc, gruppi)
    for r in righe:
        r['link'] = _link(r['chiave'], anno) if r['chiave'].startswith('voce:') else ''
    return render(request, 'contabilita/flussi.html', {
        'anno': anno,
        'vista': vista,
        'colonne': [g[0] for g in gruppi],
        'righe': righe,
        'per_conto': fc['per_conto'],
        'serie_json': _serie_json(fc['serie']),
        'etichette_json': json.dumps(ce_srv.NOMI_MESI),
        'da_classificare': MovimentoFinanziario.objects.filter(voce__isnull=True, data__year=anno).count(),
    })


@accesso
def dettaglio(request):
    """Documenti che compongono una riga del conto economico."""
    anno = _anno(request)
    chiave = request.GET.get('chiave', '')
    voce = None
    if chiave.startswith('voce:'):
        voce = get_object_or_404(VoceConto, pk=chiave.split(':')[1] or 0)
    dal, al = date(anno, 1, 1), date(anno, 12, 31)
    righe_fatture = (RigaFatturaPassiva.objects.filter(fattura__competenza_al__gte=dal, fattura__competenza_dal__lte=al)
                     .select_related('fattura__fornitore'))
    righe_fatture = righe_fatture.filter(voce=voce) if voce else righe_fatture.filter(voce__isnull=True)
    documenti = []
    for r in righe_fatture:
        quote = ce_srv.quote_competenza(r.imponibile, r.fattura.competenza_dal, r.fattura.competenza_al)
        documenti.append({'data': r.fattura.data, 'tipo': 'Fattura', 'url_fattura': r.fattura.pk,
                          'descrizione': f'{r.fattura.fornitore} n. {r.fattura.numero} - {r.descrizione}',
                          'importo': sum((q for m, q in quote.items() if m.year == anno), Decimal('0'))})
    movimenti = []
    if voce:
        movimenti = (MovimentoFinanziario.objects.filter(voce=voce, data__gte=dal, data__lte=al)
                     .select_related('conto').prefetch_related('scadenze'))
    for m in movimenti:
        economico = voce.economica and not m.scadenze.all()
        documenti.append({'data': m.data, 'tipo': f'Movimento {m.conto}', 'url_movimento': m.pk,
                          'descrizione': m.descrizione, 'importo': (m.importo if voce.sezione in ('ricavi', 'non_economico') else -m.importo),
                          'escluso': not economico})
    documenti.sort(key=lambda d: d['data'])
    return render(request, 'contabilita/dettaglio.html', {
        'anno': anno,
        'voce': voce,
        'documenti': documenti,
        'totale': sum((d['importo'] for d in documenti if not d.get('escluso')), Decimal('0')),
    })


@accesso
def cespiti(request):
    if request.method == 'POST':
        p = request.POST
        if p.get('azione') == 'salva':
            cespite = Cespite.objects.filter(pk=p.get('id') or 0).first() or Cespite()
            valore, data = _decimale(p.get('valore')), _data(p.get('data_acquisto'))
            anni = int(p['anni']) if p.get('anni', '').isdigit() else 0
            if not p.get('descrizione', '').strip() or not valore or not data or not 1 <= anni <= 50 \
                    or p.get('centro') not in dict(CENTRI):
                messages.error(request, 'Cespite non salvato: servono descrizione, valore, data e anni (1-50).')
            else:
                cespite.descrizione, cespite.valore, cespite.data_acquisto = p['descrizione'].strip()[:120], valore, data
                cespite.anni, cespite.centro, cespite.note = anni, p['centro'], p.get('note', '').strip()[:255]
                cespite.save()
                messages.success(request, f'Cespite "{cespite}" salvato.')
        elif p.get('azione') == 'elimina':
            Cespite.objects.filter(pk=p.get('cespite')).delete()
            messages.success(request, 'Cespite eliminato.')
        return redirect('contabilita:cespiti')
    oggi = timezone.localdate().replace(day=1)
    elenco = list(Cespite.objects.all())
    for c in elenco:
        c.ammortizzato = c.ammortizzato_al(oggi)
        c.residuo = c.valore - c.ammortizzato
        c.quota_anno = sum((c.quota(date(oggi.year, m, 1)) for m in range(1, 13)), Decimal('0'))
    return render(request, 'contabilita/cespiti.html', {
        'cespiti': elenco,
        'centri': CENTRI,
        'oggi': oggi,
        'totali': {k: sum((getattr(c, k) for c in elenco), Decimal('0'))
                   for k in ('valore', 'ammortizzato', 'residuo', 'quota_anno')},
    })
