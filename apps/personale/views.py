"""Costo del personale (Amministrazione): mese, dipendenti, anno.
Accesso con la sezione 'personale' (Configurazione > Permessi)."""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import calcolo, importa_excel
from .models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese


def ha_personale(user):
    from apps.auth_system.sezioni import ha_accesso
    return ha_accesso(user, 'personale')


def accesso(vista):
    return login_required(user_passes_test(ha_personale)(vista))


def _mese_da_get(request):
    try:
        return datetime.strptime(request.GET.get('mese', ''), '%Y-%m').date()
    except ValueError:
        return calcolo.primo_del_mese(timezone.localdate())


def _decimale(testo, campo, errori, minimo=None):
    """'' -> None; numero con virgola o punto; errore se non valido."""
    t = (testo or '').strip().replace(',', '.')
    if t == '':
        return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        errori.append(f'{campo}: "{testo}" non e\' un numero')
        return None
    if minimo is not None and v < minimo:
        errori.append(f'{campo}: non puo\' essere minore di {minimo}')
        return None
    return v


def _url_mese(mese):
    return f"{reverse('personale:mese')}?mese={mese:%Y-%m}"


@accesso
def mese(request):
    mese_data = _mese_da_get(request)
    mese_obj = MesePersonale.objects.filter(mese=mese_data).first()
    if request.method == 'POST':
        azione = request.POST.get('azione')
        if azione == 'crea':
            return _crea_mese(request, mese_data)
        if mese_obj is None:
            return redirect(_url_mese(mese_data))
        if azione == 'aggiungi':
            dip = get_object_or_404(Dipendente, pk=request.POST.get('dipendente'))
            ultimo = mese_obj.righe.order_by('-ordine').values_list('ordine', flat=True).first() or 0
            RigaMese.objects.get_or_create(mese=mese_obj, dipendente=dip, defaults={'ordine': ultimo + 1})
            messages.success(request, f'{dip} aggiunto al mese.')
            return redirect(_url_mese(mese_data))
        if azione == 'togli':
            riga = get_object_or_404(RigaMese, pk=request.POST.get('riga'), mese=mese_obj)
            riga.delete()
            messages.success(request, f'{riga.dipendente} tolto dal mese (le presenze restano salvate).')
            return redirect(_url_mese(mese_data))
        errori, inseriti = _salva_mese(request, mese_obj)
        if not errori:
            messages.success(request, 'Mese salvato.')
            return redirect(_url_mese(mese_data))
        messages.error(request, 'Non salvato, correggi: ' + '; '.join(errori[:8])
                       + (f' (e altri {len(errori) - 8})' if len(errori) > 8 else ''))
        contesto = _contesto_mese(mese_data, mese_obj)
        _rimetti_inseriti(contesto, inseriti)
        return render(request, 'personale/mese.html', contesto)
    return render(request, 'personale/mese.html', _contesto_mese(mese_data, mese_obj))


def _contesto_mese(mese_data, mese_obj):
    contesto = {
        'mese_data': mese_data,
        'nome_mese': f'{calcolo.NOMI_MESI[mese_data.month - 1]} {mese_data.year}',
        'mese_prec': calcolo.mese_precedente(mese_data),
        'mese_succ': calcolo.mese_successivo(mese_data),
        'mese_obj': mese_obj,
        'dipendenti_attivi': Dipendente.objects.filter(attivo=True),
    }
    if mese_obj:
        c = calcolo.calcola_mese(mese_obj)
        contesto['c'] = c
        contesto['ore_ferie_txt'] = calcolo.testo_numero(mese_obj.ore_giorno_ferie)
        contesto['ore_malattia_txt'] = calcolo.testo_numero(mese_obj.ore_giorno_malattia)
        nel_mese = {r['dipendente'].pk for r in c['righe']}
        contesto['da_aggiungere'] = Dipendente.objects.exclude(pk__in=nel_mese).order_by('-attivo', 'ordine', 'nome')
    return contesto


@transaction.atomic
def _crea_mese(request, mese_data):
    mese_obj, creato = MesePersonale.objects.get_or_create(mese=mese_data, defaults={'aggiornato_da': request.user})
    if creato:
        for i, dip in enumerate(Dipendente.objects.filter(attivo=True)):
            RigaMese.objects.create(mese=mese_obj, dipendente=dip, ordine=i)
        messages.success(request, 'Mese creato con i dipendenti attivi.')
    return redirect(_url_mese(mese_data))


@transaction.atomic
def _salva_mese(request, mese_obj):
    """Salva calendario e campi del mese. Se c'e' un errore non salva
    nulla e ritorna gli errori con i valori inseriti, da rimostrare."""
    p = request.POST
    errori, inseriti = [], {}
    giorni = calcolo.giorni_del_mese(mese_obj.mese)
    ferie = _decimale(p.get('ore_giorno_ferie'), 'Ore/giorno ferie', errori, Decimal('0'))
    malattia = _decimale(p.get('ore_giorno_malattia'), 'Ore/giorno malattia', errori, Decimal('0'))
    celle, campi = [], []
    for riga in mese_obj.righe.select_related('dipendente'):
        nome = riga.dipendente.nome
        valori_riga = {'celle': [], 'campi': {}}
        for g in giorni:
            testo = p.get(f'c_{riga.pk}_{g.day}', '')
            valori_riga['celle'].append(testo)
            try:
                celle.append((riga.dipendente, g, *calcolo.leggi_cella(testo)))
            except ValueError as e:
                errori.append(f'{nome} {g:%d/%m} {e}')
        valori = {}
        for campo, etichetta, minimo in (('coeff', 'coeff. lordo', Decimal('1')), ('busta', 'busta paga', Decimal('0')),
                                         ('bonus', 'bonus', None), ('pagato', 'pagato', Decimal('0'))):
            testo = p.get(f'{campo}_{riga.pk}', '')
            valori_riga['campi'][campo] = testo
            valori[campo] = _decimale(testo, f'{nome} {etichetta}', errori, minimo)
        campi.append((riga, valori))
        inseriti[riga.pk] = valori_riga
    if errori:
        transaction.set_rollback(True)
        return errori, inseriti

    mese_obj.ore_giorno_ferie = ferie if ferie is not None else Decimal('8')
    mese_obj.ore_giorno_malattia = malattia if malattia is not None else Decimal('8')
    mese_obj.note = p.get('note', '').strip()
    mese_obj.aggiornato_da = request.user
    mese_obj.save()
    for riga, v in campi:
        riga.coefficiente_lordo = v['coeff']
        riga.busta_paga, riga.bonus, riga.pagato = v['busta'], v['bonus'], v['pagato']
        riga.save()
    esistenti = {(pr.dipendente_id, pr.data): pr for pr in Presenza.objects.filter(
        dipendente_id__in=[r.dipendente_id for r, _ in campi], data__gte=giorni[0], data__lte=giorni[-1])}
    for dip, g, ore, assenza in celle:
        pr = esistenti.get((dip.pk, g))
        if ore is None and not assenza:
            if pr:
                pr.delete()
        elif pr is None:
            Presenza.objects.create(dipendente=dip, data=g, ore=ore, assenza=assenza)
        elif pr.ore != ore or pr.assenza != assenza:
            # corretta a mano: anche una timbratura diventa manuale
            pr.ore, pr.assenza, pr.origine = ore, assenza, 'manuale'
            pr.save()
    return [], {}


def _rimetti_inseriti(contesto, inseriti):
    """Dopo un errore il calendario rimostra quello che era stato scritto."""
    for r in contesto.get('c', {}).get('righe', []):
        v = inseriti.get(r['riga'].pk)
        if v:
            for cella, testo in zip(r['celle'], v['celle']):
                cella['v'] = testo
            r['inp'] = v['campi']


@accesso
def dipendenti(request):
    if request.method == 'POST':
        return _azione_dipendenti(request)
    oggi = timezone.localdate()
    elenco = list(Dipendente.objects.prefetch_related('parametri').select_related('utente'))
    for d in elenco:
        d.in_vigore = calcolo.parametri_per(d, oggi, list(d.parametri.all()))
    return render(request, 'personale/dipendenti.html', {
        'dipendenti': elenco,
        'utenti': User.objects.filter(is_active=True, groups__isnull=False).distinct().order_by('username'),
        'oggi': oggi,
    })


def _azione_dipendenti(request):
    p = request.POST
    azione = p.get('azione')
    torna = redirect('personale:dipendenti')
    if azione == 'importa':
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'Scegli un file Excel.')
            return torna
        try:
            esito = importa_excel.importa(file)
        except importa_excel.ErroreImport as e:
            messages.error(request, str(e))
            return torna
        messages.success(request, esito['messaggio'])
        for avviso in esito['avvisi']:
            messages.warning(request, avviso)
        return redirect(_url_mese(esito['mese'])) if esito['tipo'] == 'mese' else torna

    if azione == 'salva_dipendente':
        dip = get_object_or_404(Dipendente, pk=p['id']) if p.get('id') else Dipendente()
        dip.nome = p.get('nome', '').strip()
        dip.tipo = p.get('tipo') if p.get('tipo') in dict(Dipendente.TIPO_CHOICES) else 'contrattualizzato'
        dip.attivo = p.get('attivo') == '1'
        dip.note = p.get('note', '').strip()[:200]
        dip.utente = User.objects.filter(pk=p.get('utente') or None).first()
        if not dip.nome:
            messages.error(request, 'Il nome e\' obbligatorio.')
            return torna
        if Dipendente.objects.filter(nome__iexact=dip.nome).exclude(pk=dip.pk).exists():
            messages.error(request, f'Esiste gia\' un dipendente "{dip.nome}".')
            return torna
        if dip.utente and Dipendente.objects.filter(utente=dip.utente).exclude(pk=dip.pk).exists():
            messages.error(request, f'L\'utente {dip.utente} e\' gia\' collegato a un altro dipendente.')
            return torna
        if dip.pk is None:
            dip.ordine = (Dipendente.objects.order_by('-ordine').values_list('ordine', flat=True).first() or 0) + 1
        dip.save()
        messages.success(request, f'{dip} salvato.')
        return torna

    if azione == 'aggiungi_parametro':
        dip = get_object_or_404(Dipendente, pk=p.get('dipendente'))
        errori = []
        try:
            dal = datetime.strptime(p.get('valido_dal', ''), '%Y-%m-%d').date()
        except ValueError:
            dal = None
            errori.append('data "valido dal" mancante')
        tariffa = _decimale(p.get('tariffa_oraria'), 'tariffa', errori, Decimal('0'))
        coeff = _decimale(p.get('coefficiente_lordo'), 'coefficiente lordo', errori, Decimal('1'))
        if tariffa is None and not errori:
            errori.append('tariffa mancante')
        if errori:
            messages.error(request, 'Parametri non salvati: ' + '; '.join(errori))
            return torna
        try:
            with transaction.atomic():
                ParametroRetributivo.objects.create(dipendente=dip, valido_dal=dal, tariffa_oraria=tariffa,
                                                    coefficiente_lordo=coeff or Decimal('1'),
                                                    note=p.get('note', '').strip()[:200])
        except IntegrityError:
            messages.error(request, f'{dip} ha gia\' parametri validi dal {dal:%d/%m/%Y}: elimina quella riga prima.')
            return torna
        messages.success(request, f'Parametri di {dip} dal {dal:%d/%m/%Y} aggiunti.')
        return torna

    if azione == 'elimina_parametro':
        par = get_object_or_404(ParametroRetributivo, pk=p.get('parametro'))
        par.delete()
        messages.success(request, f'Parametri di {par.dipendente} dal {par.valido_dal:%d/%m/%Y} eliminati.')
        return torna
    return torna


@accesso
def anno(request):
    try:
        anno_n = int(request.GET.get('anno', ''))
    except ValueError:
        anno_n = timezone.localdate().year
    a = calcolo.andamento_anno(anno_n)
    grafico = {
        'etichette': [m['nome'][:3] for m in a['mesi']],
        'costo': [float(m['costo']) for m in a['mesi']],
        'netto': [float(m['netto']) for m in a['mesi']],
        'costo_ora': [float(m['costo_ora']) for m in a['mesi']],
    }
    return render(request, 'personale/anno.html', {
        'a': a, 'anno': anno_n, 'grafico_json': json.dumps(grafico),
        'anno_prec': anno_n - 1, 'anno_succ': anno_n + 1,
    })

