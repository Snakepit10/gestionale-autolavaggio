import json
from collections import OrderedDict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.auth_system.sezioni import ha_accesso
from apps.cq.models import PostazioneCQ

from . import services
from .models import (UNITA_CONTENUTO_CHOICES, Articolo, Assegnazione, Consegna, Fornitore, Movimento, OrdineFornitore,
                     RigaConsegna, RigaOrdineFornitore, StatoAssegnazione)


# ---------------------------------------------------------------------------
# Aiuti
# ---------------------------------------------------------------------------

def magazzino_required(view):
    """Sezione Magazzino (il middleware la controlla gia' sugli indirizzi;
    qui vale anche per le chiamate dirette)."""
    @login_required
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not ha_accesso(request.user, 'magazzino'):
            if request.method == 'POST':
                return _errore('Non hai accesso al Magazzino.', 403)
            return render(request, 'auth_system/accesso_negato.html', {'sezione': 'Magazzino'}, status=403)
        return view(request, *args, **kwargs)
    return inner


def _errore(msg, status=400):
    return JsonResponse({'success': False, 'error': msg}, status=status)


def _dati(request):
    try:
        return json.loads(request.body or '{}')
    except ValueError:
        return {}


def _intero(valore, default=0):
    try:
        return int(str(valore).strip())
    except (TypeError, ValueError):
        return default


def _decimale(valore, default=Decimal('0')):
    try:
        return Decimal(str(valore).replace(',', '.').strip() or '0')
    except (InvalidOperation, TypeError):
        return default


def _data(valore, default=None):
    try:
        return datetime.strptime(valore, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def _operatori():
    """Utenti del gestionale a cui si assegna merce."""
    return (User.objects.filter(is_active=True)
            .filter(Q(is_staff=True) | Q(groups__isnull=False)).distinct()
            .order_by('first_name', 'last_name', 'username'))


def _categorie_articoli():
    return sorted(set(Articolo.objects.exclude(categoria='').values_list('categoria', flat=True)),
                  key=str.lower)


def _articoli_json(solo_attivi=True):
    qs = Articolo.objects.all()
    if solo_attivi:
        qs = qs.filter(attivo=True)
    return [{'id': a.pk, 'nome': a.nome, 'tipo': a.tipo, 'unita': a.unita, 'quantita': a.quantita,
             'traccia': a.traccia_scorte, 'fornitore': a.fornitore_id,
             # prezzo come lo si indica: al pezzo o al kg/litro
             'costo': _fmt(a.costo_indicato), 'unita_prezzo': a.unita_prezzo,
             'fattore': str(a.fattore_prezzo), 'pezzo': _descrizione_pezzo(a)}
            for a in qs.order_by('nome')]


def _fmt(valore):
    """Decimale senza zeri inutili (8.2000 -> '8.2')."""
    testo = format(Decimal(valore).normalize(), 'f')
    return testo


def _dati_modifica(a):
    """Valori del modale di modifica articolo (numeri non localizzati)."""
    return json.dumps({
        'nome': a.nome, 'codice': a.codice, 'tipo': a.tipo, 'categoria': a.categoria, 'unita': a.unita,
        'contenuto': _fmt(a.contenuto) if a.contenuto else '', 'unita_contenuto': a.unita_contenuto,
        'prezzo_per': a.prezzo_per, 'scorta_minima': a.scorta_minima, 'traccia_scorte': a.traccia_scorte,
        'costo': _fmt(a.costo_indicato), 'fornitore': a.fornitore_id or '', 'attivo': a.attivo,
        'note': a.note, 'unita_prezzo': a.unita_prezzo,
    })


def _descrizione_pezzo(a):
    if not a.ha_contenuto:
        return ''
    return f'{_fmt(a.contenuto)} {a.unita_contenuto}'



def _contesto_base(attiva):
    return {
        'scheda': attiva,
        'n_segnalazioni': Assegnazione.objects.filter(da_gestire=True).count(),
    }


# ---------------------------------------------------------------------------
# Articoli
# ---------------------------------------------------------------------------

@magazzino_required
def articoli(request):
    qs = Articolo.objects.select_related('fornitore', 'prodotto')
    tipo = request.GET.get('tipo', '')
    categoria = request.GET.get('categoria', '')
    cerca = request.GET.get('q', '').strip()
    sotto = request.GET.get('sotto') == '1'
    attivi = request.GET.get('tutti') != '1'
    if tipo:
        qs = qs.filter(tipo=tipo)
    if categoria:
        qs = qs.filter(categoria=categoria)
    if cerca:
        qs = qs.filter(Q(nome__icontains=cerca) | Q(codice__icontains=cerca))
    if attivi:
        qs = qs.filter(attivo=True)
    if sotto:
        qs = qs.filter(traccia_scorte=True, quantita__lte=F('scorta_minima'))
    lista = list(qs.order_by('tipo', 'categoria', 'nome'))

    assegnati = dict(Assegnazione.objects.filter(stato__in=Assegnazione.APERTI)
                     .values('articolo').annotate(t=Sum('quantita')).values_list('articolo', 't'))
    for a in lista:
        a.n_assegnati = assegnati.get(a.pk, 0)
        a.dati_modifica = _dati_modifica(a)

    ctx = _contesto_base('articoli')
    ctx.update({
        'articoli': lista,
        'tipi': Articolo.TIPO_CHOICES,
        'unita_contenuto': UNITA_CONTENUTO_CHOICES,
        'categorie': _categorie_articoli(),
        'fornitori': Fornitore.objects.filter(attivo=True),
        'filtro': {'tipo': tipo, 'categoria': categoria, 'q': cerca, 'sotto': sotto, 'tutti': not attivi},
        'valore_totale': sum((a.valore for a in lista), Decimal('0')),
        'n_sotto_scorta': Articolo.objects.filter(attivo=True, traccia_scorte=True,
                                                  quantita__lte=F('scorta_minima')).count(),
    })
    return render(request, 'magazzino/articoli.html', ctx)


@magazzino_required
def articolo_scheda(request, pk):
    articolo = get_object_or_404(Articolo.objects.select_related('fornitore', 'prodotto'), pk=pk)
    articolo.dati_modifica = _dati_modifica(articolo)
    ctx = _contesto_base('articoli')
    ctx.update({
        'articolo': articolo,
        'movimenti': articolo.movimenti.select_related('operatore', 'ordine', 'consegna',
                                                       'assegnazione')[:200],
        'assegnazioni': articolo.assegnazioni.filter(stato__in=Assegnazione.APERTI)
                                             .select_related('utente', 'postazione'),
        'in_arrivo': [r for r in RigaOrdineFornitore.objects.filter(
            articolo=articolo, ordine__stato__in=('inviato', 'parziale')).select_related('ordine')
            if r.residuo > 0],
        'tipi': Articolo.TIPO_CHOICES,
        'unita_contenuto': UNITA_CONTENUTO_CHOICES,
        'categorie': _categorie_articoli(),
        'fornitori': Fornitore.objects.filter(attivo=True),
    })
    return render(request, 'magazzino/articolo.html', ctx)


@magazzino_required
def articolo_salva(request):
    """Crea o modifica un articolo (JSON)."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    d = _dati(request)
    nome = (d.get('nome') or '').strip()
    if not nome:
        return _errore('Il nome è obbligatorio')
    tipo = d.get('tipo') or 'consumabile'
    if tipo not in dict(Articolo.TIPO_CHOICES):
        return _errore('Tipo non valido')
    pk = d.get('id')
    articolo = get_object_or_404(Articolo, pk=pk) if pk else Articolo()
    articolo.nome = nome
    articolo.codice = (d.get('codice') or '').strip()
    articolo.tipo = tipo
    articolo.categoria = (d.get('categoria') or '').strip()
    articolo.unita = (d.get('unita') or 'pz').strip() or 'pz'
    contenuto = _decimale(d.get('contenuto'))
    articolo.contenuto = contenuto if contenuto > 0 else None
    articolo.unita_contenuto = d.get('unita_contenuto') or ''
    if articolo.unita_contenuto not in dict(UNITA_CONTENUTO_CHOICES):
        articolo.unita_contenuto = ''
    if bool(articolo.contenuto) != bool(articolo.unita_contenuto):
        return _errore('Indica sia il contenuto del pezzo sia la sua unità (es. 25 kg)')
    articolo.prezzo_per = 'contenuto' if d.get('prezzo_per') == 'contenuto' else 'pezzo'
    if articolo.prezzo_per == 'contenuto' and not articolo.ha_contenuto:
        return _errore('Per indicare il prezzo al kg/litro serve il contenuto del pezzo')
    articolo.scorta_minima = max(_intero(d.get('scorta_minima')), 0)
    articolo.traccia_scorte = bool(d.get('traccia_scorte', True))
    articolo.costo = articolo.a_pezzo(_decimale(d.get('costo')))
    articolo.fornitore_id = d.get('fornitore') or None
    articolo.attivo = bool(d.get('attivo', True))
    articolo.note = d.get('note') or ''
    with transaction.atomic():
        nuovo = articolo.pk is None
        articolo.save()
        iniziale = _intero(d.get('quantita_iniziale'))
        if nuovo and iniziale > 0:
            services.movimenta(articolo, iniziale, 'carico', operatore=request.user,
                               nota='Quantità iniziale')
        services.sincronizza_prodotto(articolo)
    return JsonResponse({'success': True, 'id': articolo.pk})


@magazzino_required
def articolo_movimento(request, pk):
    """Carico manuale, scarto o rettifica di inventario (JSON)."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    articolo = get_object_or_404(Articolo, pk=pk)
    d = _dati(request)
    tipo = d.get('tipo')
    quantita = _intero(d.get('quantita'), None)
    nota = (d.get('nota') or '').strip()
    if quantita is None:
        return _errore('Quantità non valida')
    if tipo == 'rettifica':
        if quantita < 0:
            return _errore('La quantità contata non può essere negativa')
        mov = services.rettifica(articolo, quantita, operatore=request.user, nota=nota)
    elif tipo in ('carico', 'scarto'):
        if quantita <= 0:
            return _errore('Quantità non valida')
        if tipo == 'carico' and _decimale(d.get('costo')) > 0:
            articolo.costo = articolo.a_pezzo(_decimale(d.get('costo')))
            articolo.save(update_fields=['costo'])
        mov = services.movimenta(articolo, quantita if tipo == 'carico' else -quantita, tipo,
                                 operatore=request.user, nota=nota)
    else:
        return _errore('Tipo di movimento non valido')
    return JsonResponse({'success': True, 'quantita': mov.quantita_dopo})


@magazzino_required
def movimenti(request):
    qs = Movimento.objects.select_related('articolo', 'operatore', 'ordine', 'consegna',
                                          'assegnazione__utente', 'assegnazione__postazione')
    tipo = request.GET.get('tipo', '')
    articolo = request.GET.get('articolo', '')
    dal = _data(request.GET.get('dal'))
    al = _data(request.GET.get('al'))
    if tipo:
        qs = qs.filter(tipo=tipo)
    if articolo:
        qs = qs.filter(articolo_id=articolo)
    if dal:
        qs = qs.filter(data__date__gte=dal)
    if al:
        qs = qs.filter(data__date__lte=al)
    pagina = Paginator(qs, 100).get_page(request.GET.get('page'))
    ctx = _contesto_base('movimenti')
    ctx.update({
        'pagina': pagina,
        'tipi': Movimento.TIPO_CHOICES,
        'articoli': Articolo.objects.order_by('nome'),
        'filtro': {'tipo': tipo, 'articolo': articolo, 'dal': dal, 'al': al},
        'query': '&'.join(f'{k}={v}' for k, v in request.GET.items() if k != 'page' and v),
    })
    return render(request, 'magazzino/movimenti.html', ctx)


# ---------------------------------------------------------------------------
# Fornitori
# ---------------------------------------------------------------------------

@magazzino_required
def fornitori(request):
    lista = list(Fornitore.objects.all())
    ordini_aperti = dict(OrdineFornitore.objects.filter(stato__in=('inviato', 'parziale'))
                         .values('fornitore').annotate(n=Count('pk')).values_list('fornitore', 'n'))
    for f in lista:
        f.n_ordini_aperti = ordini_aperti.get(f.pk, 0)
        f.n_articoli = f.articoli.count()
    ctx = _contesto_base('fornitori')
    ctx['fornitori'] = lista
    return render(request, 'magazzino/fornitori.html', ctx)


@magazzino_required
def fornitore_salva(request):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    d = _dati(request)
    nome = (d.get('ragione_sociale') or '').strip()
    if not nome:
        return _errore('La ragione sociale è obbligatoria')
    f = get_object_or_404(Fornitore, pk=d['id']) if d.get('id') else Fornitore()
    f.ragione_sociale = nome
    for campo in ('partita_iva', 'referente', 'telefono', 'email', 'note'):
        setattr(f, campo, (d.get(campo) or '').strip())
    f.attivo = bool(d.get('attivo', True))
    f.save()
    return JsonResponse({'success': True, 'id': f.pk})


# ---------------------------------------------------------------------------
# Ordini ai fornitori
# ---------------------------------------------------------------------------

@magazzino_required
def ordini(request):
    qs = OrdineFornitore.objects.select_related('fornitore').prefetch_related('righe__articolo')
    stato = request.GET.get('stato', '')
    fornitore = request.GET.get('fornitore', '')
    if stato == 'aperti':
        qs = qs.filter(stato__in=('bozza', 'inviato', 'parziale'))
    elif stato:
        qs = qs.filter(stato=stato)
    if fornitore:
        qs = qs.filter(fornitore_id=fornitore)
    ctx = _contesto_base('ordini')
    ctx.update({
        'ordini': qs[:300],
        'stati': OrdineFornitore.STATO_CHOICES,
        'fornitori': Fornitore.objects.all(),
        'filtro': {'stato': stato, 'fornitore': fornitore},
    })
    return render(request, 'magazzino/ordini.html', ctx)


@magazzino_required
def ordine_scheda(request, pk=None):
    """Nuovo ordine o modifica/consultazione di uno esistente."""
    ordine = get_object_or_404(OrdineFornitore.objects.select_related('fornitore'), pk=pk) if pk else None
    righe = []
    if ordine:
        righe = [{'articolo': r.articolo_id, 'quantita': r.quantita, 'prezzo': _fmt(r.prezzo_indicato),
                  'ricevuto': r.ricevuto} for r in ordine.righe.select_related('articolo')]
    ctx = _contesto_base('ordini')
    ctx.update({
        'ordine': ordine,
        'righe_json': json.dumps(righe),
        'articoli_json': json.dumps(_articoli_json()),
        'fornitori': Fornitore.objects.filter(Q(attivo=True) | Q(pk=getattr(ordine, 'fornitore_id', None))),
        'consegne': ordine.consegne.prefetch_related('righe__articolo') if ordine else [],
        'oggi': timezone.localdate(),
    })
    return render(request, 'magazzino/ordine.html', ctx)


@magazzino_required
def ordine_salva(request):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    d = _dati(request)
    fornitore = Fornitore.objects.filter(pk=d.get('fornitore')).first()
    if fornitore is None:
        return _errore('Scegli il fornitore')
    righe = []
    for r in d.get('righe') or []:
        articolo = Articolo.objects.filter(pk=r.get('articolo')).first()
        quantita = _intero(r.get('quantita'))
        if articolo and quantita > 0:
            righe.append((articolo, quantita, articolo.a_pezzo(_decimale(r.get('prezzo')))))
    if not righe:
        return _errore("Aggiungi almeno un articolo con quantità")
    with transaction.atomic():
        if d.get('id'):
            ordine = get_object_or_404(OrdineFornitore.objects.select_for_update(), pk=d['id'])
            if not ordine.modificabile:
                return _errore("L'ordine non è più modificabile")
            if ordine.consegne.exists():
                return _errore("L'ordine ha già delle consegne: non si modificano le righe")
        else:
            ordine = OrdineFornitore(creato_da=request.user)
        ordine.fornitore = fornitore
        ordine.data = _data(d.get('data'), timezone.localdate())
        ordine.note = d.get('note') or ''
        ordine.save()
        ordine.righe.all().delete()
        for articolo, quantita, prezzo in righe:
            RigaOrdineFornitore.objects.create(ordine=ordine, articolo=articolo,
                                               quantita=quantita, prezzo=prezzo)
    return JsonResponse({'success': True, 'id': ordine.pk})


@magazzino_required
def ordine_stato(request, pk):
    """Segna inviato, rimetti in bozza o annulla."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    ordine = get_object_or_404(OrdineFornitore, pk=pk)
    stato = _dati(request).get('stato')
    if stato == 'inviato' and ordine.stato == 'bozza':
        ordine.stato = 'inviato'
    elif stato == 'bozza' and ordine.stato == 'inviato':
        ordine.stato = 'bozza'
    elif stato == 'annullato' and ordine.stato in ('bozza', 'inviato', 'parziale'):
        ordine.stato = 'annullato'
    elif stato == 'riapri' and ordine.stato == 'annullato':
        ordine.stato = 'inviato'
        ordine.save(update_fields=['stato'])
        ordine.aggiorna_stato()
        return JsonResponse({'success': True, 'stato': ordine.stato})
    else:
        return _errore('Cambio di stato non consentito')
    ordine.save(update_fields=['stato'])
    return JsonResponse({'success': True, 'stato': ordine.stato})


@magazzino_required
def ordine_elimina(request, pk):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    ordine = get_object_or_404(OrdineFornitore, pk=pk)
    if ordine.stato != 'bozza':
        return _errore('Si eliminano solo gli ordini in bozza (gli altri si annullano)')
    ordine.delete()
    return JsonResponse({'success': True})


@magazzino_required
def ordine_stampa(request, pk):
    ordine = get_object_or_404(OrdineFornitore.objects.select_related('fornitore'), pk=pk)
    return render(request, 'magazzino/ordine_stampa.html', {
        'ordine': ordine, 'righe': ordine.righe.select_related('articolo')})


@magazzino_required
def proponi_sotto_scorta(request):
    """Righe proposte per un ordine: articoli sotto scorta (del fornitore
    scelto, se indicato) per tornare al doppio della scorta minima,
    tolto quello gia' in arrivo."""
    qs = Articolo.objects.filter(attivo=True, traccia_scorte=True, quantita__lte=F('scorta_minima'))
    fornitore = request.GET.get('fornitore')
    if fornitore:
        qs = qs.filter(Q(fornitore_id=fornitore) | Q(fornitore__isnull=True))
    righe = []
    for a in qs.order_by('nome'):
        in_arrivo = sum(r.residuo for r in RigaOrdineFornitore.objects.filter(
            articolo=a, ordine__stato__in=('inviato', 'parziale')))
        quantita = max(a.scorta_minima * 2, 1) - a.quantita - in_arrivo
        if quantita > 0:
            righe.append({'articolo': a.pk, 'quantita': quantita, 'prezzo': _fmt(a.costo_indicato)})
    return JsonResponse({'success': True, 'righe': righe})


# ---------------------------------------------------------------------------
# Consegne (registro)
# ---------------------------------------------------------------------------

@magazzino_required
def consegne(request):
    qs = Consegna.objects.select_related('fornitore', 'ordine', 'registrata_da').prefetch_related('righe__articolo')
    fornitore = request.GET.get('fornitore', '')
    dal = _data(request.GET.get('dal'))
    al = _data(request.GET.get('al'))
    if fornitore:
        qs = qs.filter(fornitore_id=fornitore)
    if dal:
        qs = qs.filter(data__gte=dal)
    if al:
        qs = qs.filter(data__lte=al)
    ordini_aperti = OrdineFornitore.objects.filter(stato__in=('inviato', 'parziale')).select_related('fornitore')
    ctx = _contesto_base('consegne')
    ctx.update({
        'consegne': qs[:300],
        'fornitori': Fornitore.objects.all(),
        'filtro': {'fornitore': fornitore, 'dal': dal, 'al': al},
        'ordini_aperti': ordini_aperti,
        'articoli_json': json.dumps(_articoli_json()),
        'oggi': timezone.localdate(),
        'da_ordine': request.GET.get('ordine', ''),
    })
    return render(request, 'magazzino/consegne.html', ctx)


@magazzino_required
def ordine_residuo(request, pk):
    """Righe ancora da ricevere di un ordine (per precompilare la consegna)."""
    ordine = get_object_or_404(OrdineFornitore, pk=pk)
    righe = [{'riga_ordine': r.pk, 'articolo': r.articolo_id, 'nome': r.articolo.nome,
              'ordinato': r.quantita, 'quantita': r.residuo, 'prezzo': _fmt(r.prezzo_indicato),
              'unita_prezzo': r.articolo.unita_prezzo, 'pezzo': _descrizione_pezzo(r.articolo)}
             for r in ordine.righe.select_related('articolo') if r.residuo > 0]
    return JsonResponse({'success': True, 'fornitore': ordine.fornitore_id, 'righe': righe})


@magazzino_required
def consegna_registra(request):
    """Registra una consegna: carica il magazzino e aggiorna l'ordine."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    d = _dati(request)
    ordine = OrdineFornitore.objects.filter(pk=d.get('ordine')).first() if d.get('ordine') else None
    fornitore = ordine.fornitore if ordine else Fornitore.objects.filter(pk=d.get('fornitore')).first()
    if fornitore is None:
        return _errore('Scegli il fornitore o l\'ordine')
    if ordine and ordine.stato not in ('inviato', 'parziale'):
        return _errore("L'ordine non è in attesa di consegna")
    righe = []
    for r in d.get('righe') or []:
        quantita = _intero(r.get('quantita'))
        if quantita <= 0:
            continue
        riga_ordine = None
        if ordine and r.get('riga_ordine'):
            riga_ordine = ordine.righe.filter(pk=r['riga_ordine']).first()
        articolo = riga_ordine.articolo if riga_ordine else Articolo.objects.filter(pk=r.get('articolo')).first()
        if articolo is None:
            continue
        righe.append((articolo, riga_ordine, quantita, articolo.a_pezzo(_decimale(r.get('prezzo')))))
    if not righe:
        return _errore('Nessun articolo con quantità ricevuta')
    with transaction.atomic():
        consegna = Consegna.objects.create(
            fornitore=fornitore, ordine=ordine, data=_data(d.get('data'), timezone.localdate()),
            numero_ddt=(d.get('numero_ddt') or '').strip(), note=d.get('note') or '',
            registrata_da=request.user)
        for articolo, riga_ordine, quantita, prezzo in righe:
            RigaConsegna.objects.create(consegna=consegna, articolo=articolo, riga_ordine=riga_ordine,
                                        quantita=quantita, prezzo=prezzo)
            if prezzo > 0 and articolo.costo != prezzo:
                articolo.costo = prezzo
                articolo.save(update_fields=['costo'])
            services.movimenta(articolo, quantita, 'consegna', operatore=request.user, consegna=consegna,
                               nota=f'{fornitore}' + (f' DDT {consegna.numero_ddt}' if consegna.numero_ddt else ''))
        if ordine:
            ordine.aggiorna_stato()
    return JsonResponse({'success': True, 'id': consegna.pk,
                         'stato_ordine': ordine.stato if ordine else None})


@magazzino_required
def consegna_elimina(request, pk):
    """Annulla una consegna registrata per errore: toglie la merce dal
    magazzino e riapre l'ordine."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    consegna = get_object_or_404(Consegna, pk=pk)
    ordine = consegna.ordine
    with transaction.atomic():
        for r in consegna.righe.select_related('articolo'):
            services.movimenta(r.articolo, -r.quantita, 'rettifica', operatore=request.user,
                               nota=f'Annullata consegna {consegna.fornitore} del {consegna.data:%d/%m/%Y}')
        consegna.delete()
        if ordine:
            ordine.aggiorna_stato()
    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Assegnazioni e dotazioni
# ---------------------------------------------------------------------------

def _per_destinatario(assegnazioni):
    """{('utente'|'postazione', id): {'nome', 'tipo', 'righe'}} in ordine."""
    gruppi = OrderedDict()
    for a in assegnazioni:
        chiave = ('postazione', a.postazione_id) if a.postazione_id else ('utente', a.utente_id)
        if chiave not in gruppi:
            gruppi[chiave] = {'tipo': chiave[0], 'nome': a.destinatario, 'righe': [],
                              'valore': Decimal('0')}
        gruppi[chiave]['righe'].append(a)
        gruppi[chiave]['valore'] += (a.articolo.costo * a.quantita).quantize(Decimal('0.01'))
    return list(gruppi.values())


@magazzino_required
def assegnazioni(request):
    vista = request.GET.get('vista', 'postazioni')
    aperte = (Assegnazione.objects.filter(stato__in=Assegnazione.APERTI)
              .select_related('articolo', 'utente', 'postazione', 'assegnata_da'))
    if vista == 'operatori':
        aperte = aperte.filter(utente__isnull=False).order_by('utente__first_name', 'utente__username',
                                                               'articolo__nome')
    else:
        vista = 'postazioni'
        aperte = aperte.filter(postazione__isnull=False).order_by('postazione__ordine', 'articolo__nome')
    ctx = _contesto_base('assegnazioni')
    ctx.update({
        'vista': vista,
        'gruppi': _per_destinatario(aperte),
        'operatori': _operatori(),
        'postazioni': PostazioneCQ.objects.filter(attiva=True),
        'articoli_json': json.dumps(_articoli_json()),
        'stati': Assegnazione.STATO_CHOICES,
        'recenti': StatoAssegnazione.objects.select_related(
            'assegnazione__articolo', 'assegnazione__utente', 'assegnazione__postazione', 'utente')[:30],
    })
    return render(request, 'magazzino/assegnazioni.html', ctx)


@magazzino_required
def assegnazione_nuova(request):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    d = _dati(request)
    articolo = Articolo.objects.filter(pk=d.get('articolo')).first()
    if articolo is None:
        return _errore("Scegli l'articolo")
    utente = User.objects.filter(pk=d.get('utente')).first() if d.get('utente') else None
    postazione = PostazioneCQ.objects.filter(pk=d.get('postazione')).first() if d.get('postazione') else None
    quantita = _intero(d.get('quantita'), 1)
    if articolo.traccia_scorte and quantita > articolo.quantita and not d.get('forza'):
        return JsonResponse({'success': False, 'conferma': True,
                             'error': f'In magazzino ci sono solo {articolo.quantita} {articolo.unita}. '
                                      'Assegnare comunque?'}, status=400)
    try:
        a = services.assegna(articolo, quantita, utente=utente, postazione=postazione, da=request.user,
                             note=d.get('note') or '', in_apertura=bool(d.get('in_apertura', True)),
                             in_chiusura=bool(d.get('in_chiusura', True)))
    except ValueError as e:
        return _errore(str(e))
    return JsonResponse({'success': True, 'id': a.pk})


@magazzino_required
def assegnazione_modifica(request, pk):
    """Quando controllarla in checklist e note (le quantita' si cambiano
    con restituzioni e segnalazioni, per lasciare traccia)."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    a = get_object_or_404(Assegnazione, pk=pk)
    d = _dati(request)
    a.in_apertura = bool(d.get('in_apertura', a.in_apertura))
    a.in_chiusura = bool(d.get('in_chiusura', a.in_chiusura))
    a.note = (d.get('note') if d.get('note') is not None else a.note)[:255]
    a.save(update_fields=['in_apertura', 'in_chiusura', 'note'])
    services.sincronizza_checklist(a)
    return JsonResponse({'success': True})


def _cambia_stato_json(request, a, origine):
    d = _dati(request)
    stato = d.get('stato')
    consentiti = (dict(Assegnazione.STATO_CHOICES) if origine == 'magazzino'
                  else Assegnazione.SEGNALABILI)
    if stato not in consentiti or stato == 'sostituito':
        return _errore('Stato non valido')
    try:
        cambiata = services.cambia_stato(a, stato, quantita=d.get('quantita') or None,
                                         utente=request.user, origine=origine,
                                         nota=(d.get('nota') or '').strip())
    except ValueError as e:
        return _errore(str(e))
    if cambiata is None:
        return _errore("Lo stato è già questo o l'assegnazione è chiusa")
    return JsonResponse({'success': True, 'id': cambiata.pk, 'stato': cambiata.stato})


@magazzino_required
def assegnazione_stato(request, pk):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    return _cambia_stato_json(request, get_object_or_404(Assegnazione, pk=pk), 'magazzino')


@magazzino_required
def segnalazioni(request):
    ctx = _contesto_base('segnalazioni')
    ctx.update({
        'segnalazioni': (Assegnazione.objects.filter(da_gestire=True)
                         .select_related('articolo', 'utente', 'postazione')
                         .prefetch_related('storico__utente')),
        'gestite': (StatoAssegnazione.objects.filter(origine__in=('operatore', 'checklist'),
                                                     assegnazione__da_gestire=False)
                    .select_related('assegnazione__articolo', 'assegnazione__utente',
                                    'assegnazione__postazione', 'utente')[:30]),
        'stati_restituzione': [('restituito', 'Rientrato in magazzino')],
    })
    return render(request, 'magazzino/segnalazioni.html', ctx)


@magazzino_required
def assegnazione_sostituisci(request, pk):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    a = get_object_or_404(Assegnazione, pk=pk)
    nuova = services.sostituisci(a, da=request.user, nota=(_dati(request).get('nota') or '').strip())
    return JsonResponse({'success': True, 'id': nuova.pk})


@magazzino_required
def assegnazione_gestita(request, pk):
    """Presa visione della segnalazione senza sostituire."""
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    Assegnazione.objects.filter(pk=pk).update(da_gestire=False)
    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# La mia dotazione (operatori, sezione Il Mio Turno)
# ---------------------------------------------------------------------------

@login_required
def mia_dotazione(request):
    if not ha_accesso(request.user, 'mio_turno') and not ha_accesso(request.user, 'magazzino'):
        return render(request, 'auth_system/accesso_negato.html', {'sezione': 'Il Mio Turno'}, status=403)
    postazioni_turno = services.postazioni_del_turno(request.user)
    base = Assegnazione.objects.select_related('articolo', 'postazione', 'utente')
    personali = base.filter(utente=request.user, stato__in=Assegnazione.APERTI).order_by('articolo__nome')
    di_postazione = (base.filter(postazione_id__in=postazioni_turno, stato__in=Assegnazione.APERTI)
                     .order_by('postazione__ordine', 'articolo__nome'))
    chiuse = (base.filter(Q(utente=request.user) | Q(postazione_id__in=postazioni_turno))
              .exclude(stato__in=Assegnazione.APERTI).order_by('-chiusa_il')[:15])
    return render(request, 'magazzino/mia_dotazione.html', {
        'personali': personali,
        'postazioni': _per_destinatario(di_postazione),
        'chiuse': chiuse,
        'stati_segnalabili': [(k, v) for k, v in Assegnazione.STATO_CHOICES if k in Assegnazione.SEGNALABILI],
        'in_turno': bool(postazioni_turno),
    })


@login_required
def mia_dotazione_stato(request, pk):
    if request.method != 'POST':
        return _errore('Metodo non consentito', 405)
    a = get_object_or_404(Assegnazione, pk=pk)
    if not services.puo_segnalare(request.user, a):
        return _errore('Non è una tua assegnazione né di una postazione del tuo turno', 403)
    return _cambia_stato_json(request, a, 'operatore')


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def dati_report(dal, al):
    """Consumi, segnalazioni e acquisti del periodo."""
    periodo = Q(data__date__gte=dal, data__date__lte=al)

    # Consumi: merce uscita verso operatori/postazioni meno quella rientrata
    consumi = OrderedDict()
    movs = (Movimento.objects.filter(periodo, tipo__in=('assegnazione', 'rientro'), assegnazione__isnull=False)
            .select_related('articolo', 'assegnazione__utente', 'assegnazione__postazione')
            .order_by('assegnazione__postazione__ordine', 'assegnazione__utente__username', 'articolo__nome'))
    for m in movs:
        dest = m.assegnazione.destinatario
        gruppo = consumi.setdefault(dest, {'nome': dest, 'tipo': 'postazione' if m.assegnazione.postazione_id
                                           else 'utente', 'articoli': OrderedDict(), 'costo': Decimal('0')})
        riga = gruppo['articoli'].setdefault(m.articolo_id, {'articolo': m.articolo, 'quantita': 0,
                                                            'costo': Decimal('0')})
        riga['quantita'] -= m.quantita
        riga['costo'] -= m.quantita * m.articolo.costo
        gruppo['costo'] -= m.quantita * m.articolo.costo
    consumi = [dict(g, articoli=[r for r in g['articoli'].values() if r['quantita']])
               for g in consumi.values()]
    consumi = [g for g in consumi if g['articoli']]

    # Segnalazioni nel periodo (usurati, rotti, smarriti, esauriti)
    problemi = OrderedDict()
    for s in (StatoAssegnazione.objects.filter(periodo, a_stato__in=Assegnazione.SEGNALABILI)
              .select_related('assegnazione__articolo', 'assegnazione__utente', 'assegnazione__postazione')):
        a = s.assegnazione
        chiave = (a.destinatario, a.articolo_id, s.a_stato)
        riga = problemi.setdefault(chiave, {'destinatario': a.destinatario, 'articolo': a.articolo,
                                            'stato': s.a_stato_display, 'codice': s.a_stato,
                                            'quantita': 0, 'costo': Decimal('0')})
        riga['quantita'] += s.quantita
        riga['costo'] += s.quantita * a.articolo.costo
    problemi = sorted(problemi.values(), key=lambda r: (-r['costo'], r['destinatario']))

    # Acquisti per fornitore (consegne ricevute)
    acquisti = OrderedDict()
    for r in (RigaConsegna.objects.filter(consegna__data__gte=dal, consegna__data__lte=al)
              .select_related('consegna__fornitore')):
        f = r.consegna.fornitore
        riga = acquisti.setdefault(f.pk, {'fornitore': f, 'consegne': set(), 'importo': Decimal('0')})
        riga['consegne'].add(r.consegna_id)
        riga['importo'] += r.prezzo * r.quantita
    acquisti = sorted(({**r, 'consegne': len(r['consegne'])} for r in acquisti.values()),
                      key=lambda r: -r['importo'])

    # Costo dei consumabili per lavaggio
    from apps.ordini.models import Ordine

    costo_consumabili = -(Movimento.objects.filter(periodo, tipo__in=('assegnazione', 'rientro'),
                                                   articolo__tipo='consumabile')
                          .aggregate(t=Sum(F('quantita') * F('articolo__costo')))['t'] or 0)
    n_lavaggi = Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al,
                                      vendita_prodotti=False).count()

    return {
        'dal': dal, 'al': al,
        'consumi': consumi,
        'totale_consumi': sum((g['costo'] for g in consumi), Decimal('0')),
        'problemi': problemi,
        'totale_problemi': sum((r['costo'] for r in problemi), Decimal('0')),
        'acquisti': acquisti,
        'totale_acquisti': sum((r['importo'] for r in acquisti), Decimal('0')),
        'costo_consumabili': Decimal(costo_consumabili).quantize(Decimal('0.01')),
        'n_lavaggi': n_lavaggi,
        'costo_per_lavaggio': (Decimal(costo_consumabili) / n_lavaggi).quantize(Decimal('0.01'))
        if n_lavaggi else None,
        'mese_prec': (dal.replace(day=1) - timedelta(days=1)).replace(day=1),
    }


@magazzino_required
def report(request):
    oggi = timezone.localdate()
    dal = _data(request.GET.get('dal'), oggi.replace(day=1))
    al = _data(request.GET.get('al'), oggi)
    ctx = _contesto_base('report')
    ctx.update(dati_report(dal, al))
    return render(request, 'magazzino/report.html', ctx)


# ---------------------------------------------------------------------------
# Vecchia Gestione Scorte
# ---------------------------------------------------------------------------

def vecchie_scorte(request, *args, **kwargs):
    if 'alert' in request.path:
        return redirect('/magazzino/?sotto=1')
    if 'movimenti' in request.path:
        return redirect('magazzino:movimenti')
    return redirect('magazzino:articoli')
