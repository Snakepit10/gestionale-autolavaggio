"""Prima nota (Amministrazione > Contabilita'): movimenti dei conti,
import degli estratti, abbinamento dei pagamenti alle fatture, conti e
regole di classificazione."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import estratti
from . import prima_nota as srv
from .models import ContoFinanziario, MovimentoFinanziario, RegolaMovimento, ScadenzaFattura, VoceConto
from .views import _data, _decimale, _mese, accesso, campi_ripartizione, ripartizione_da_post


def mese_successivo(mese):
    return (mese.replace(day=28) + timedelta(days=4)).replace(day=1)


# ---------------------------------------------------------------- prima nota

@accesso
def prima_nota(request):
    if request.method == 'POST':
        _azione(request)
        return redirect(request.get_full_path())

    oggi = timezone.localdate()
    mese = _mese(request.GET.get('mese'), oggi.replace(day=1))
    fine_mese = mese_successivo(mese) - timedelta(days=1)
    stato = request.GET.get('stato', '')
    conti = list(ContoFinanziario.objects.filter(attivo=True))
    conto = next((c for c in conti if str(c.pk) == request.GET.get('conto', '')), None)

    qs = MovimentoFinanziario.objects.select_related('conto', 'voce').prefetch_related('scadenze__fattura')
    if conto:
        qs = qs.filter(conto=conto)
    if stato == 'da_classificare':
        qs = qs.filter(voce__isnull=True)
    else:
        qs = qs.filter(data__gte=mese, data__lte=fine_mese)
    cerca = request.GET.get('q', '').strip()
    if cerca:
        qs = qs.filter(Q(descrizione__icontains=cerca) | Q(causale__icontains=cerca) | Q(note__icontains=cerca))
    movimenti = list(qs.order_by('-data', '-id')[:1000])

    saldi = [{'conto': c, 'inizio': c.saldo_al(mese - timedelta(days=1)), 'fine': c.saldo_al(fine_mese)}
             for c in ([conto] if conto else conti)]
    return render(request, 'contabilita/prima_nota.html', {
        'movimenti': movimenti,
        'conti': conti,
        'conto': conto,
        'mese': mese,
        'mese_prec': (mese - timedelta(days=1)).replace(day=1),
        'mese_succ': mese_successivo(mese),
        'stato': stato,
        'cerca': cerca,
        'entrate': sum((m.importo for m in movimenti if m.importo > 0), Decimal('0')),
        'uscite': sum((m.importo for m in movimenti if m.importo < 0), Decimal('0')),
        'saldi': saldi,
        'da_classificare': MovimentoFinanziario.objects.filter(voce__isnull=True).count(),
        'proposte': srv.proponi_abbinamenti(),
        'voci': VoceConto.objects.filter(attiva=True),
        'segni': RegolaMovimento.SEGNI,
        'oggi': oggi,
    })


def _azione(request):
    p = request.POST
    azione = p.get('azione')
    if azione == 'importa':
        conto = ContoFinanziario.objects.filter(pk=p.get('conto') or 0).first()
        file = request.FILES.get('file')
        if not conto or not file:
            messages.error(request, 'Scegli il conto e il file dell\'estratto.')
            return
        esito = estratti.importa_estratto(conto, file.name, file.read())
        for errore in esito.errori:
            messages.error(request, f'{file.name}: {errore}')
        if not esito.errori:
            testo = f'{conto}: {esito.nuovi} movimenti nuovi dal {esito.dal:%d/%m/%Y} al {esito.al:%d/%m/%Y}'
            if esito.doppi:
                testo += f', {esito.doppi} gia\' presenti'
            if esito.classificati:
                testo += f', {esito.classificati} classificati dalle regole'
            messages.success(request, testo + '.')
    elif azione == 'classifica':
        voci = {str(v.pk): v for v in VoceConto.objects.all()}
        cambiati = []
        for m in MovimentoFinanziario.objects.filter(pk__in=p.getlist('mov')).select_related('voce'):
            voce = voci.get(p.get(f'voce_{m.pk}', ''))
            if voce != m.voce:
                m.voce, m.regola = voce, None
                cambiati.append(m)
        MovimentoFinanziario.objects.bulk_update(cambiati, ['voce', 'regola'])
        messages.success(request, f'{len(cambiati)} movimenti aggiornati.')
    elif azione == 'abbina':
        mov = get_object_or_404(MovimentoFinanziario, pk=p.get('movimento'))
        scadenza = get_object_or_404(ScadenzaFattura, pk=p.get('scadenza'))
        srv.abbina(mov, [scadenza])
        messages.success(request, f'Pagamento abbinato a {scadenza.fattura}.')
    elif azione == 'abbina_tutte':
        proposte = srv.proponi_abbinamenti()
        for pr in proposte:
            srv.abbina(pr['movimento'], [pr['scadenza']])
        messages.success(request, f'{len(proposte)} pagamenti abbinati.')
    elif azione == 'nuovo':
        _nuovo_movimento(request)
    elif azione == 'regola':
        regola = salva_regola(request)
        if regola:
            messages.success(request, f'Regola "{regola.testo}" creata: {applica_regole()} movimenti classificati.')


def _nuovo_movimento(request):
    p = request.POST
    conto = ContoFinanziario.objects.filter(pk=p.get('conto') or 0).first()
    data, importo = _data(p.get('data')), _decimale(p.get('importo'))
    if not conto or not data or not importo:
        messages.error(request, 'Movimento non inserito: servono conto, data e importo (negativo per le uscite).')
        return
    MovimentoFinanziario.objects.create(
        conto=conto, data=data, importo=importo, descrizione=p.get('descrizione', '').strip(),
        voce=VoceConto.objects.filter(pk=p.get('voce') or 0).first(), origine='manuale',
        impronta=f'manuale|{uuid.uuid4().hex}')
    messages.success(request, 'Movimento inserito.')


def applica_regole():
    """Applica le regole ai movimenti ancora da classificare."""
    regole = list(RegolaMovimento.objects.select_related('voce'))
    classificati = [m for m in MovimentoFinanziario.objects.filter(voce__isnull=True) if m.classifica(regole)]
    MovimentoFinanziario.objects.bulk_update(classificati, ['voce', 'ripartizione', 'regola'])
    return len(classificati)


@accesso
def movimento(request, pk):
    mov = get_object_or_404(MovimentoFinanziario.objects.select_related('conto', 'voce', 'controparte__conto'), pk=pk)
    if request.method == 'POST':
        risposta = _azione_movimento(request, mov)
        return risposta or redirect('contabilita:movimento', pk=mov.pk)

    candidate = []
    if mov.importo < 0:
        candidate = sorted(srv.scadenze_aperte(), key=lambda s: (
            abs(s.importo + mov.importo), abs((mov.data - (s.data or s.fattura.data)).days)))[:15]
    return render(request, 'contabilita/movimento.html', {
        'mov': mov,
        'voci': VoceConto.objects.filter(attiva=True),
        'campi_rip': campi_ripartizione(mov.ripartizione, mov.voce.ripartizione if mov.voce else {}),
        'abbinate': mov.scadenze.select_related('fattura__fornitore'),
        'candidate': candidate,
        'altri_conti': ContoFinanziario.objects.filter(attivo=True).exclude(pk=mov.conto_id),
    })


def _azione_movimento(request, mov):
    p = request.POST
    azione = p.get('azione')
    if azione == 'salva':
        try:
            mov.ripartizione = ripartizione_da_post(p, 'rip')
        except ValueError as e:
            messages.error(request, f'Non salvato: {e}.')
            return None
        mov.voce = VoceConto.objects.filter(pk=p.get('voce') or 0).first()
        mov.note = p.get('note', '').strip()[:255]
        mov.regola = None
        mov.save()
        messages.success(request, 'Movimento salvato.')
    elif azione == 'abbina':
        scelte = list(srv.scadenze_aperte().filter(pk__in=p.getlist('scadenza')))
        totale = sum((s.importo for s in scelte), Decimal('0'))
        if not scelte:
            messages.error(request, 'Scegli almeno una scadenza.')
        elif abs(totale + mov.importo) > Decimal('0.01'):
            messages.error(request, f'Le scadenze scelte fanno €{totale:.2f}, il movimento €{-mov.importo:.2f}.')
        else:
            srv.abbina(mov, scelte)
            messages.success(request, f'Abbinato a {len(scelte)} scadenz{"a" if len(scelte) == 1 else "e"}.')
    elif azione == 'togli':
        srv.togli_abbinamento(get_object_or_404(ScadenzaFattura, pk=p.get('scadenza'), movimento=mov))
    elif azione == 'giroconto':
        conto = ContoFinanziario.objects.filter(pk=p.get('conto') or 0).exclude(pk=mov.conto_id).first()
        if conto and not mov.controparte_id:
            srv.giroconto(mov, conto)
            messages.success(request, f'Giroconto registrato anche su {conto}.')
    elif azione == 'elimina' and mov.origine == 'manuale':
        mov.delete()
        messages.success(request, 'Movimento eliminato.')
        return redirect('contabilita:prima_nota')
    return None


# ---------------------------------------------------------------- conti

@accesso
def conti(request):
    if request.method == 'POST':
        p = request.POST
        if p.get('azione') == 'salva_conto':
            _salva_conto(request)
        elif p.get('azione') == 'genera_cassa':
            conto = get_object_or_404(ContoFinanziario, pk=p.get('conto'), tipo='cassa')
            dal, al = _mese(p.get('dal')), _mese(p.get('al'))
            if not dal or not al or al < dal:
                messages.error(request, 'Mesi non validi.')
            else:
                n = srv.genera_cassa(conto, dal, mese_successivo(al) - timedelta(days=1))
                messages.success(request, f'{conto}: {n} movimenti scritti da quadrature, spese e ticket.')
        return redirect('contabilita:conti')

    oggi = timezone.localdate()
    elenco = list(ContoFinanziario.objects.all())
    for c in elenco:
        c.saldo_oggi = c.saldo_al(oggi)
        c.n_movimenti = c.movimenti.count()
        c.ultimo = c.movimenti.order_by('-data').values_list('data', flat=True).first()
        c.verifica = _verifica_saldo(c)
    return render(request, 'contabilita/conti.html', {
        'conti': elenco,
        'tipi': ContoFinanziario.TIPI,
        'oggi': oggi,
        'totale': sum((c.saldo_oggi for c in elenco if c.attivo), Decimal('0')),
    })


def _verifica_saldo(conto):
    """Confronto col saldo dell'estratto nell'ultimo giorno che lo riporta:
    a fine giornata il saldo calcolato deve essere uno dei saldi di quel
    giorno (l'ordine delle righe nello stesso giorno varia fra le banche)."""
    ultimo = conto.movimenti.filter(saldo_estratto__isnull=False).order_by('-data').first()
    if not ultimo:
        return None
    saldi = set(conto.movimenti.filter(data=ultimo.data, saldo_estratto__isnull=False)
                .values_list('saldo_estratto', flat=True))
    calcolato = conto.saldo_al(ultimo.data)
    return {'data': ultimo.data, 'calcolato': calcolato, 'quadra': calcolato in saldi,
            'estratto': min(saldi, key=lambda s: abs(s - calcolato))}


def _salva_conto(request):
    p = request.POST
    conto = ContoFinanziario.objects.filter(pk=p.get('id') or 0).first() or ContoFinanziario()
    nome = p.get('nome', '').strip()[:60]
    data = _data(p.get('data_saldo_iniziale'))
    saldo = _decimale(p.get('saldo_iniziale')) if p.get('saldo_iniziale', '').strip() else Decimal('0')
    if not nome or not data or saldo is None or p.get('tipo') not in dict(ContoFinanziario.TIPI):
        messages.error(request, 'Conto non salvato: servono nome, tipo, data e saldo iniziale validi.')
        return
    if ContoFinanziario.objects.filter(nome=nome).exclude(pk=conto.pk).exists():
        messages.error(request, f'Esiste gia\' un conto "{nome}".')
        return
    conto.nome, conto.tipo, conto.iban = nome, p['tipo'], p.get('iban', '').strip()[:34]
    conto.saldo_iniziale, conto.data_saldo_iniziale, conto.attivo = saldo, data, bool(p.get('attivo'))
    conto.save()
    messages.success(request, f'Conto "{conto}" salvato.')


# ---------------------------------------------------------------- regole

def salva_regola(request):
    p = request.POST
    regola = RegolaMovimento.objects.filter(pk=p.get('id') or 0).first() or RegolaMovimento()
    testo = p.get('testo', '').strip()[:100]
    voce = VoceConto.objects.filter(pk=p.get('voce') or 0).first()
    if not testo or not voce or p.get('segno', '') not in dict(RegolaMovimento.SEGNI):
        messages.error(request, 'Regola non salvata: servono il testo da cercare e la voce.')
        return None
    regola.testo, regola.voce, regola.segno = testo, voce, p.get('segno', '')
    regola.conto = ContoFinanziario.objects.filter(pk=p.get('conto') or 0).first()
    if p.get('ordine', '').isdigit():
        regola.ordine = int(p['ordine'])
    regola.save()
    return regola


@accesso
def regole(request):
    if request.method == 'POST':
        azione = request.POST.get('azione')
        if azione == 'salva':
            if salva_regola(request):
                messages.success(request, 'Regola salvata.')
        elif azione == 'elimina':
            RegolaMovimento.objects.filter(pk=request.POST.get('regola')).delete()
            messages.success(request, 'Regola eliminata.')
        elif azione == 'applica':
            messages.success(request, f'{applica_regole()} movimenti classificati.')
        return redirect('contabilita:regole')
    elenco = list(RegolaMovimento.objects.select_related('voce', 'conto'))
    for r in elenco:
        r.n_movimenti = r.movimentofinanziario_set.count()
    return render(request, 'contabilita/regole.html', {
        'regole': elenco,
        'voci': VoceConto.objects.filter(attiva=True),
        'conti': ContoFinanziario.objects.filter(attivo=True),
        'segni': RegolaMovimento.SEGNI,
        'da_classificare': MovimentoFinanziario.objects.filter(voce__isnull=True).count(),
    })
