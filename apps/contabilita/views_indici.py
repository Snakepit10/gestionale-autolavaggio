"""Indici e KPI, ipotesi per l'indebitamento massimo e finanziamenti in
corso (Amministrazione > Contabilita')."""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from .conto_economico import BASI_RICAVI, NOMI_MESI
from .indici import indici as calcola_indici
from .models import Finanziamento, ParametriFinanziari, aggiungi_mesi
from .views import _data, _decimale, accesso

# campo, minimo, massimo
LIMITI_PARAMETRI = [
    ('multiplo_ebitda', Decimal('0.5'), Decimal('10')),
    ('dscr_minimo', Decimal('1'), Decimal('3')),
    ('tasso_nuovo_debito', Decimal('0'), Decimal('25')),
    ('capex_mantenimento', Decimal('0'), Decimal('100')),
    ('aliquota_imposte', Decimal('0'), Decimal('60')),
    ('multiplo_valutazione', Decimal('0.5'), Decimal('15')),
]


@accesso
def indici(request):
    if request.method == 'POST':
        par = ParametriFinanziari.attuali()
        p, errori = request.POST, []
        for campo, minimo, massimo in LIMITI_PARAMETRI:
            valore = _decimale(p.get(campo))
            if valore is None or not minimo <= valore <= massimo:
                errori.append(campo)
            else:
                setattr(par, campo, valore)
        durata = p.get('durata_nuovo_debito', '')
        if durata.isdigit() and 1 <= int(durata) <= 30:
            par.durata_nuovo_debito = int(durata)
        else:
            errori.append('durata_nuovo_debito')
        if errori:
            messages.error(request, 'Ipotesi non salvate: controlla i valori (' + ', '.join(errori) + ').')
        else:
            par.save()
            messages.success(request, 'Ipotesi salvate.')
        return redirect(f"{request.path}?anno={p.get('anno', '')}&ricavi={p.get('ricavi', '')}")

    oggi = timezone.localdate()
    try:
        anno = int(request.GET.get('anno', oggi.year))
    except ValueError:
        anno = oggi.year
    base = request.GET.get('ricavi', 'rilevato')
    if base not in BASI_RICAVI:
        base = 'rilevato'
    risultato = calcola_indici(anno, base, oggi)
    periodo = ''
    if risultato:
        m = risultato['mesi']
        periodo = NOMI_MESI[m[0].month - 1] + (f'–{NOMI_MESI[m[-1].month - 1]}' if len(m) > 1 else '') + f' {anno}'
    return render(request, 'contabilita/indici.html', {
        'r': risultato,
        'anno': anno,
        'base': base,
        'basi': BASI_RICAVI.items(),
        'periodo': periodo,
        'parametri': ParametriFinanziari.attuali(),
    })


@accesso
def finanziamenti(request):
    if request.method == 'POST':
        p = request.POST
        if p.get('azione') == 'salva':
            f = Finanziamento.objects.filter(pk=p.get('id') or 0).first() or Finanziamento()
            importo, tasso, data = _decimale(p.get('importo')), _decimale(p.get('tasso') or '0'), _data(p.get('data_prima_rata'))
            durata = int(p['durata_mesi']) if p.get('durata_mesi', '').isdigit() else -1
            rata = _decimale(p.get('rata'))
            if not p.get('descrizione', '').strip() or not importo or importo <= 0 or tasso is None or not 0 <= tasso <= 30 \
                    or not data or not 0 <= durata <= 600 or p.get('tipo') not in dict(Finanziamento.TIPI) \
                    or (rata is not None and rata <= 0):
                messages.error(request, 'Finanziamento non salvato: servono descrizione, importo, tasso (0-30), '
                                        'data della prima rata e numero di rate (0 = a revoca).')
            else:
                f.descrizione, f.ente, f.tipo = p['descrizione'].strip()[:120], p.get('ente', '').strip()[:80], p['tipo']
                f.importo, f.tasso, f.durata_mesi, f.data_prima_rata, f.rata = importo, tasso, durata, data, rata
                f.note = p.get('note', '').strip()[:255]
                f.save()
                messages.success(request, f'Finanziamento "{f}" salvato.')
        elif p.get('azione') == 'elimina':
            Finanziamento.objects.filter(pk=p.get('finanziamento')).delete()
            messages.success(request, 'Finanziamento eliminato.')
        return redirect('contabilita:finanziamenti')

    oggi = timezone.localdate()
    fine_12 = aggiungi_mesi(oggi, 12) - timedelta(days=1)
    elenco = list(Finanziamento.objects.all())
    for f in elenco:
        f.residuo = f.residuo_al(oggi)
        f.capitale_12, f.interessi_12 = f.servizio(oggi + timedelta(days=1), fine_12)
    return render(request, 'contabilita/finanziamenti.html', {
        'finanziamenti': elenco,
        'tipi': Finanziamento.TIPI,
        'oggi': oggi,
        'totali': {k: sum((getattr(f, k) for f in elenco), Decimal('0'))
                   for k in ('importo', 'residuo', 'capitale_12', 'interessi_12')},
        'rate_mese': sum((f.rata_mensile for f in elenco if f.residuo > 0), Decimal('0')),
    })
