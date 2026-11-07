"""Prima nota (Amministrazione > Contabilita'): movimenti dei conti,
import degli estratti, abbinamento dei pagamenti alle fatture, conti e
regole di classificazione."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import estratti
from . import prima_nota as srv
from .conto_economico import pagamenti_senza_fattura
from .indici import euro
from .models import (ContoFinanziario, ModelloEstratto, MovimentoFinanziario, RegolaMovimento, ScadenzaFattura,
                     VoceConto)
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
    elif stato == 'senza_fattura':
        anno = request.GET.get('anno', '')
        dal_al = (date(int(anno), 1, 1), date(int(anno), 12, 31)) if anno.isdigit() else (None, None)
        qs = qs.filter(pk__in=pagamenti_senza_fattura(*dal_al).values('pk'))
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
        'modelli': estratti.modelli_disponibili(),
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
        modello = estratti.modello_da_chiave(p.get('modello', ''))   # vuoto: quello del conto
        esito = estratti.importa_estratto(conto, file.name, file.read(), modello)
        for errore in esito.errori:
            messages.error(request, f'{file.name}: {errore}')
        if esito.quadra_col_totale is False:
            messages.warning(request, 'La somma dei movimenti letti non coincide con il totale riportato nel file: '
                                      'controlla il modello di estratto.')
        if not esito.errori:
            testo = (f'{conto} ({esito.modello}): {esito.nuovi} movimenti nuovi '
                     f'dal {esito.dal:%d/%m/%Y} al {esito.al:%d/%m/%Y}')
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
        scadenze = list(ScadenzaFattura.objects.filter(pk__in=p.getlist('scadenza')).select_related('fattura__fornitore'))
        if not scadenze:
            raise Http404
        srv.abbina(mov, scadenze, srv.voce_commissioni())
        fatture = ', '.join(str(s.fattura) for s in scadenze)
        messages.success(request, f'Pagamento abbinato a {fatture}'
                                  f'{f"; €{-mov.differenza:.2f} di commissione su Spese bancarie" if mov.differenza else ""}.')
    elif azione == 'abbina_tutte':
        proposte = srv.proponi_abbinamenti()
        commissioni = srv.voce_commissioni()
        for pr in proposte:
            srv.abbina(pr['movimento'], pr['scadenze'], commissioni)
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
    mov = get_object_or_404(MovimentoFinanziario.objects.select_related('conto', 'voce', 'voce_differenza', 'controparte__conto'),
                            pk=pk)
    if request.method == 'POST':
        risposta = _azione_movimento(request, mov)
        return risposta or redirect('contabilita:movimento', pk=mov.pk)

    candidate = []
    cerca = request.GET.get('cerca', '').strip()
    if mov.importo < 0:
        aperte = srv.scadenze_aperte()
        if cerca:
            aperte = aperte.filter(Q(fattura__fornitore__ragione_sociale__icontains=cerca) | Q(fattura__numero__icontains=cerca))
        aperte = list(aperte)
        testo = f'{mov.causale} {mov.descrizione}'
        for s in aperte:
            s.nome_trovato = srv.nome_nel_testo(s.fattura.fornitore.ragione_sociale, testo)
        # prima l'importo esatto, poi i fornitori nominati nella descrizione, poi l'importo piu' vicino
        candidate = sorted(aperte, key=lambda s: (
            abs(s.importo + mov.importo) > Decimal('0.01'), not s.nome_trovato,
            abs(s.importo + mov.importo), abs((mov.data - (s.data or s.fattura.data)).days)))
        candidate = candidate[:50 if cerca else 15]
    return render(request, 'contabilita/movimento.html', {
        'mov': mov,
        'voci': VoceConto.objects.filter(attiva=True),
        'campi_rip': campi_ripartizione(mov.ripartizione, mov.voce.ripartizione if mov.voce else {}),
        'abbinate': mov.scadenze.select_related('fattura__fornitore'),
        'candidate': candidate,
        'cerca': cerca,
        'voci_differenza': [v for v in VoceConto.objects.filter(attiva=True) if v.economica],
        'voce_commissioni': srv.voce_commissioni(),
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
        if not scelte:
            messages.error(request, 'Scegli almeno una scadenza.')
            return None
        tipo = p.get('tipo_differenza', 'voce')
        testo = f'Abbinato a {len(scelte)} scadenz{"a" if len(scelte) == 1 else "e"}'
        try:
            if tipo == 'contanti':
                contanti = srv.paga_resto_in_contanti(mov, scelte, _data(p.get('data_contanti')))
                testo += f'; il resto (€{-contanti.importo:.2f}) e\' un\'uscita di {contanti.conto} del {contanti.data:%d/%m/%Y}'
            elif tipo == 'resto':
                resto = srv.abbina(mov, scelte, parziale=True)
                if resto:
                    testo += f'; restano da pagare €{resto.importo:.2f} di {resto.fattura}'
            else:
                srv.abbina(mov, scelte, VoceConto.objects.filter(pk=p.get('voce_differenza') or 0).first())
                if mov.differenza:
                    testo += f'; €{abs(mov.differenza):.2f} di differenza su "{mov.voce_differenza}"'
        except ValueError as e:
            messages.error(request, f'Non abbinato: {e}.')
            return None
        messages.success(request, testo + '.')
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
                messages.success(request, f'{conto}: {n} movimenti scritti da quadrature, spese, ticket e versamenti.')
        elif p.get('azione') == 'conta_cassa':
            conto = get_object_or_404(ContoFinanziario, pk=p.get('conto'), tipo='cassa')
            giorno, contati = _data(p.get('data')), _decimale(p.get('contati'))
            if not giorno or contati is None or contati < 0 or giorno < conto.data_saldo_iniziale \
                    or giorno > timezone.localdate():
                messages.error(request, 'Conta non salvata: servono la data (dal saldo iniziale a oggi) e i contanti contati.')
            else:
                m = srv.conta_cassa(conto, giorno, contati)
                if m.importo:
                    messages.success(request, f'{conto}: differenza di {euro(m.importo, 2)} al {giorno:%d/%m/%Y}, '
                                              'registrata da classificare in prima nota (prelievi, stipendi o spese in contanti).')
                else:
                    messages.success(request, f'{conto}: la cassa quadra al {giorno:%d/%m/%Y}.')
        elif p.get('azione') == 'correggi_baps_su_mps':
            esito = srv.correggi_baps_su_mps()
            if esito:
                messages.success(request, f'Tolti {esito[0]} doppioni da MPS e spostati {esito[1]} movimenti su BAPS. '
                                          'Ora premi Aggiorna sulla cassa da gennaio a oggi: i versamenti si ricalcolano.')
        return redirect('contabilita:conti')

    oggi = timezone.localdate()
    elenco = list(ContoFinanziario.objects.all())
    modelli = estratti.modelli_disponibili()
    nomi_modelli = {m.chiave: m.nome for m in modelli}
    for c in elenco:
        c.nome_modello = nomi_modelli.get(c.modello_estratto, 'Riconoscimento automatico')
        c.saldo_oggi = c.saldo_al(oggi)
        c.n_movimenti = c.movimenti.count()
        c.ultimo = c.movimenti.order_by('-data').values_list('data', flat=True).first()
        c.verifica = _verifica_saldo(c)
        c.conta = c.ultima_conta() if c.tipo == 'cassa' else None
    return render(request, 'contabilita/conti.html', {
        'conti': elenco,
        'tipi': ContoFinanziario.TIPI,
        'modelli': modelli,
        'oggi': oggi,
        'totale': sum((c.saldo_oggi for c in elenco if c.attivo), Decimal('0')),
        'baps_su_mps': srv.movimenti_di_baps_su_mps(),
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
    cassa = p['tipo'] == 'cassa'
    conto.pos_nei_contanti_fino_al = _data(p.get('pos_nei_contanti_fino_al')) if cassa else None
    conto.stipendi_in_contanti = cassa and bool(p.get('stipendi_in_contanti'))
    modello = p.get('modello_estratto', '')
    conto.modello_estratto = modello if estratti.modello_da_chiave(modello) else ''
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
    regola.escludi = p.get('escludi', '').strip()[:100]
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


# ---------------------------------------------------------------- modelli di estratto

CAMPI_MODELLO = ['col_data', 'col_valuta', 'col_importo', 'col_dare', 'col_avere', 'col_descrizione',
                 'col_causale', 'col_saldo']


@accesso
def modelli_estratto(request):
    risultato = None
    if request.method == 'POST':
        p = request.POST
        azione = p.get('azione')
        if azione == 'salva':
            _salva_modello(request)
        elif azione == 'elimina':
            modello = ModelloEstratto.objects.filter(pk=p.get('modello')).first()
            if modello:
                ContoFinanziario.objects.filter(modello_estratto=f'pers_{modello.pk}').update(modello_estratto='')
                modello.delete()
                messages.success(request, 'Modello eliminato.')
        if azione != 'anteprima':
            return redirect('contabilita:modelli_estratto')
        # Anteprima: legge il file e mostra cosa verrebbe importato, senza importare
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'Scegli il file da provare.')
        else:
            try:
                risultato = estratti.anteprima(file.name, file.read(), estratti.modello_da_chiave(p.get('modello', '')))
                risultato['file'] = file.name
            except estratti.ErroreEstratto as e:
                messages.error(request, f'{file.name}: {e}')

    personalizzati = list(ModelloEstratto.objects.all())
    for m in personalizzati:
        m.colonne = estratti.modello_personalizzato(m).colonne()
        m.lettere = {c: estratti.lettera(getattr(m, c)) for c in CAMPI_MODELLO}
        m.conti = ContoFinanziario.objects.filter(modello_estratto=f'pers_{m.pk}')
    return render(request, 'contabilita/modelli_estratto.html', {
        'predefiniti': estratti.MODELLI_PREDEFINITI,
        'personalizzati': personalizzati,
        'modelli': estratti.modelli_disponibili(),
        'conti': ContoFinanziario.objects.exclude(modello_estratto=''),
        'risultato': risultato,
        'scelto': request.POST.get('modello', '') if risultato else '',
    })


def _salva_modello(request):
    p = request.POST
    modello = ModelloEstratto.objects.filter(pk=p.get('id') or 0).first() or ModelloEstratto()
    nome = p.get('nome', '').strip()[:60]
    errori = []
    try:
        colonne = {c: estratti.colonna(p.get(c, '')) for c in CAMPI_MODELLO}
    except ValueError as e:
        messages.error(request, f'Modello non salvato: {e}.')
        return
    if not nome:
        errori.append('manca il nome')
    elif ModelloEstratto.objects.filter(nome=nome).exclude(pk=modello.pk).exists():
        errori.append(f'esiste gia\' un modello "{nome}"')
    if not colonne['col_data']:
        errori.append('manca la colonna della data')
    if not (colonne['col_importo'] or colonne['col_dare'] or colonne['col_avere']):
        errori.append('serve la colonna dell\'importo, oppure dare e avere')
    usate = [v for v in colonne.values() if v]
    if len(usate) != len(set(usate)):
        errori.append('la stessa colonna e\' usata per due dati')
    if errori:
        messages.error(request, 'Modello non salvato: ' + '; '.join(errori) + '.')
        return
    modello.nome, modello.note = nome, p.get('note', '').strip()[:200]
    for campo, valore in colonne.items():
        setattr(modello, campo, valore)
    modello.save()
    messages.success(request, f'Modello "{modello}" salvato.')
