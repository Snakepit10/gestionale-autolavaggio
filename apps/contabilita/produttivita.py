"""Produttivita' del servito (Analitica > Produttivita').

- Andamento mese per mese: lavaggi, ore pagate, auto per ora-persona,
  persone al giorno, costo del lavoro sui ricavi, costo e risultato per
  lavaggio, ognuno confrontato con lo stesso mese dell'anno prima.
- Giorni della settimana e giorni di pioggia contro asciutti: medie per
  giorno aperto (con almeno un lavaggio).
- Mix ed extra: servizio principale = la riga di servizio col prezzo
  piu' alto, extra = le altre righe di servizio.
- Pareggio: margine di contribuzione per lavaggio = ricavo - consumi per
  lavaggio; costo per giorno-persona = (costo pieno - consumi) /
  giorni-persona; lavaggi per persona al giorno a pareggio = costo per
  giorno-persona / margine per lavaggio; per il margine obiettivo m =
  costo per giorno-persona / (margine per lavaggio - m x ricavo per
  lavaggio), dall'equazione utile = m x ricavi. Persone consigliate =
  lavaggi medi / lavaggi per persona al margine obiettivo, per eccesso.

Le ore e le persone del servito vengono dalle presenze giornaliere, per
la quota servito della ripartizione di ogni dipendente. L'ora di
creazione degli ordini non e' affidabile: niente analisi per fascia
oraria, solo per giorno."""
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

from .conto_economico import IVA, NOMI_MESI, ZERO, fine_mese

DURATA_CACHE = 60 * 60          # un'ora per i mesi gia' chiusi
VERSIONE_CACHE = 1
GIORNI_SETTIMANA = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']


def _div(a, b):
    return a / b if b else None


def carica_ripartizioni():
    from .models import RipartizionePersonale
    ripartizioni = defaultdict(list)
    for r in RipartizionePersonale.objects.order_by('-valido_dal'):
        ripartizioni[r.dipendente_id].append(r)
    return ripartizioni


def presenze_servito(dal, al):
    """{data: (ore servito, persone servito)}: ogni presenza con ore conta
    per la quota servito del dipendente in quel mese."""
    from apps.personale.models import Presenza

    from .analitica import ripartizione_dipendente
    ripartizioni = carica_ripartizioni()
    giorni = defaultdict(lambda: [ZERO, ZERO])
    for dip, giorno, ore in (Presenza.objects.filter(data__gte=dal, data__lte=al, assenza='', ore__gt=0)
                             .values_list('dipendente_id', 'data', 'ore')):
        quota = Decimal(str(ripartizione_dipendente(dip, giorno.replace(day=1), ripartizioni).get('servito', 0))) / 100
        giorni[giorno][0] += ore * quota
        giorni[giorno][1] += quota
    return {g: (o, p) for g, (o, p) in giorni.items()}


def lavaggi_per_giorno(dal, al):
    """{data: (lavaggi, ricavo netto degli ordini)}; ricavo = totale degli
    ordini / 1,22 (prodotti dentro gli ordini compresi)."""
    from apps.finanze.services.ricavi import ordini_di_lavaggio
    from apps.ordini.models import Ordine
    ordini = Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al).exclude(stato='annullato')
    ids = ordini_di_lavaggio(ordini).values('id')
    righe = (Ordine.objects.filter(id__in=ids).values('data_ora__date')
             .annotate(n=Count('id'), incasso=Sum('totale_finale')))
    return {r['data_ora__date']: (r['n'], (r['incasso'] or ZERO) / IVA) for r in righe}


# ---------------------------------------------------------------- mese per mese

def kpi_mese(mese, base):
    """Le grandezze del servito di un mese (somme, non rapporti), dalla
    stessa analitica della scheda Centri. None per i mesi futuri."""
    oggi = timezone.localdate()
    if mese > oggi:
        return None
    chiave = f'analitica_kpi_{VERSIONE_CACHE}_{base}_{mese:%Y%m}'
    chiuso = fine_mese(mese) < oggi.replace(day=1)
    if chiuso:
        dati = cache.get(chiave)
        if dati is not None:
            return dati
    from .analitica import calcola_centri
    c = calcola_centri([mese], base)
    sv = c['centri']['servito']
    presenze = presenze_servito(mese, fine_mese(mese))
    lavaggi = lavaggi_per_giorno(mese, fine_mese(mese))
    dati = {
        'lavaggi': c['dati']['ordini_lavaggio'],
        'abbonamento': c['dati']['lavaggi_abbonamento'],
        'ore': c['dati']['ore']['servito'],
        'giorni_persona': sum((p for _, p in presenze.values()), ZERO),
        'giorni_aperti': sum(1 for n, _ in lavaggi.values() if n),
        'ricavi': sv['ricavi'],
        'variabili': sv['totali']['costi_variabili'],
        'personale': sv['totali']['personale'],
        'pieno': sv['costi'] + sv['quota_comuni'],
    }
    if chiuso:
        cache.set(chiave, dati, DURATA_CACHE)
    return dati


def somma_kpi(elenco):
    elenco = [k for k in elenco if k]
    if not elenco:
        return None
    return {chiave: sum((k[chiave] for k in elenco), type(elenco[0][chiave])()) for chiave in elenco[0]}


def derivati(k):
    """Rapporti calcolati dalle somme."""
    if not k:
        return None
    return {
        'lavaggi': k['lavaggi'],
        'abbonamento': k['abbonamento'],
        'ore': k['ore'],
        'auto_ora': _div(Decimal(k['lavaggi']), k['ore']),
        'persone_giorno': _div(k['giorni_persona'], k['giorni_aperti']),
        'lavaggi_persona': _div(Decimal(k['lavaggi']), k['giorni_persona']),
        'ricavo_lavaggio': _div(k['ricavi'], k['lavaggi']),
        'costo_lavaggio': _div(k['pieno'], k['lavaggi']),
        'risultato_lavaggio': _div(k['ricavi'] - k['pieno'], k['lavaggi']),
        'lavoro_ricavi': _div(k['personale'] * 100, k['ricavi']),
        'consumi_lavaggio': _div(k['variabili'], k['lavaggi']),
        'ricavi': k['ricavi'],
        'risultato': k['ricavi'] - k['pieno'],
    }


# (chiave, nome, formato, meglio se sale)
RIGHE_ANDAMENTO = [
    ('lavaggi', 'Lavaggi', 'intero', True),
    ('abbonamento', 'di cui in abbonamento', 'intero', None),
    ('ore', 'Ore pagate servito', 'intero', None),
    ('auto_ora', 'Auto per ora-persona', 'due', True),
    ('persone_giorno', 'Persone medie al giorno', 'uno', None),
    ('lavaggi_persona', 'Lavaggi per persona al giorno', 'uno', True),
    ('ricavo_lavaggio', 'Ricavo per lavaggio', 'euro2', True),
    ('consumi_lavaggio', 'Consumi per lavaggio', 'euro2', False),
    ('costo_lavaggio', 'Costo pieno per lavaggio', 'euro2', False),
    ('risultato_lavaggio', 'Risultato per lavaggio', 'euro2', True),
    ('lavoro_ricavi', 'Costo del lavoro sui ricavi', 'pct', False),
    ('ricavi', 'Ricavi servito', 'euro', True),
    ('risultato', 'Risultato pieno servito', 'euro', True),
]


def _variazione(v, prima):
    if v is None or prima is None or not prima:
        return None
    return (Decimal(v) - Decimal(prima)) / abs(Decimal(prima)) * 100


def andamento(anno, base):
    """Tabella mese per mese dell'anno con lo stesso mese dell'anno prima.
    Il totale dell'anno prima conta solo i mesi gia' passati di quest'anno."""
    mesi = [date(anno, m, 1) for m in range(1, 13)]
    correnti = [kpi_mese(m, base) for m in mesi]
    precedenti = [kpi_mese(m.replace(year=anno - 1), base) for m in mesi]
    totale = somma_kpi(correnti)
    totale_prima = somma_kpi([p for p, c in zip(precedenti, correnti) if c])
    colonne = [derivati(k) for k in correnti] + [derivati(totale)]
    colonne_prima = [derivati(k) for k in precedenti] + [derivati(totale_prima)]
    righe = []
    for chiave, nome, formato, meglio_su in RIGHE_ANDAMENTO:
        celle = []
        for col, prima in zip(colonne, colonne_prima):
            v = col[chiave] if col else None
            p = prima[chiave] if prima else None
            var = _variazione(v, p)
            buono = None if var is None or meglio_su is None or abs(var) < Decimal('0.5') else (var > 0) == meglio_su
            celle.append({'v': v, 'prima': p, 'var': var, 'buono': buono})
        righe.append({'chiave': chiave, 'nome': nome, 'formato': formato, 'celle': celle})
    return {'mesi': [n[:3] for n in NOMI_MESI] + ['Totale'], 'righe': righe, 'anno_prima': anno - 1}


# ---------------------------------------------------------------- giorni

def _medie(giorni):
    """Medie per giorno aperto: lavaggi, persone, lavaggi per persona, ricavo."""
    n = len(giorni)
    if not n:
        return None
    lavaggi = sum(g['lavaggi'] for g in giorni)
    persone = sum((g['persone'] for g in giorni), ZERO)
    ricavo = sum((g['ricavo'] for g in giorni), ZERO)
    return {'giorni': n, 'lavaggi': Decimal(lavaggi) / n, 'persone': persone / n,
            'lavaggi_persona': _div(Decimal(lavaggi), persone), 'ricavo': ricavo / n}


def giorni_del_periodo(dal, al):
    """Una riga per ogni giorno aperto (con almeno un lavaggio) fino a ieri."""
    al = min(al, timezone.localdate() - timedelta(days=1))
    if al < dal:
        return []
    lavaggi = lavaggi_per_giorno(dal, al)
    presenze = presenze_servito(dal, al)
    return [{'data': g, 'lavaggi': n, 'ricavo': ricavo, 'persone': presenze.get(g, (ZERO, ZERO))[1]}
            for g, (n, ricavo) in sorted(lavaggi.items()) if n]


def per_giorno_settimana(giorni):
    gruppi = defaultdict(list)
    for g in giorni:
        gruppi[g['data'].weekday()].append(g)
    return [{'nome': GIORNI_SETTIMANA[i], **_medie(gruppi[i])} for i in range(7) if gruppi[i]]


def per_meteo(giorni, dal, al):
    """Medie dei giorni di pioggia e asciutti; None se il meteo non risponde."""
    from apps.personale.previsione import _meteo, piove
    if not giorni:
        return []
    meteo = _meteo(dal, min(al, giorni[-1]['data']))
    if meteo is None:
        return None
    gruppi = {'Pioggia': [], 'Asciutto': []}
    for g in giorni:
        m = meteo.get(g['data'])
        if m:
            gruppi['Pioggia' if piove(m) else 'Asciutto'].append(g)
    return [{'nome': nome, **_medie(lista)} for nome, lista in gruppi.items() if lista]


# ---------------------------------------------------------------- mix ed extra

def mix_ed_extra(dal, al):
    """Servizio principale (la riga di servizio col prezzo piu' alto) ed
    extra (le altre righe di servizio) degli ordini di lavaggio."""
    from apps.finanze.services.ricavi import ordini_di_lavaggio
    from apps.ordini.models import ItemOrdine, Ordine
    ordini = Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al).exclude(stato='annullato')
    ids = ordini_di_lavaggio(ordini).values('id')
    per_ordine = defaultdict(list)
    for ordine, titolo, prezzo, quantita in (ItemOrdine.objects.filter(ordine_id__in=ids, servizio_prodotto__tipo='servizio')
                                             .values_list('ordine_id', 'servizio_prodotto__titolo', 'prezzo_unitario',
                                                          'quantita')):
        per_ordine[ordine].append((titolo, prezzo or ZERO, quantita or 1))
    lavaggi = len(per_ordine)
    if not lavaggi:
        return None
    con_extra, ricavo_extra = 0, ZERO
    principali = defaultdict(lambda: {'n': 0, 'ricavo': ZERO})
    for righe in per_ordine.values():
        righe.sort(key=lambda r: -r[1])
        titolo, prezzo, _ = righe[0]
        principali[titolo]['n'] += 1
        principali[titolo]['ricavo'] += prezzo / IVA
        if len(righe) > 1:
            con_extra += 1
            ricavo_extra += sum((p * q for _, p, q in righe[1:]), ZERO) / IVA
    mix = sorted(({'nome': t, 'n': d['n'], 'quota': Decimal(d['n']) * 100 / lavaggi, 'ricavo': d['ricavo'] / d['n']}
                  for t, d in principali.items()), key=lambda x: -x['n'])
    return {'lavaggi': lavaggi, 'con_extra': con_extra, 'pct_extra': Decimal(con_extra) * 100 / lavaggi,
            'ricavo_extra': ricavo_extra / lavaggi, 'mix': mix[:12], 'altri': len(mix) - 12 if len(mix) > 12 else 0}


# ---------------------------------------------------------------- pareggio

def pareggio(k, margine_obiettivo):
    """Lavaggi per persona al giorno che servono a pareggio e per il margine
    obiettivo, dalle somme del periodo (kpi_mese)."""
    from apps.personale.models import ParametriPrevisione
    if not k or not k['lavaggi'] or not k['giorni_persona']:
        return None
    lavaggi = Decimal(k['lavaggi'])
    ricavo = k['ricavi'] / lavaggi
    consumi = k['variabili'] / lavaggi
    mdc = ricavo - consumi
    costo_gp = (k['pieno'] - k['variabili']) / k['giorni_persona']
    m = Decimal(margine_obiettivo) / 100
    denominatore = mdc - m * ricavo
    return {
        'ricavo': ricavo, 'consumi': consumi, 'mdc': mdc, 'costo_gp': costo_gp,
        'pareggio': costo_gp / mdc if mdc > 0 else None,
        'obiettivo': costo_gp / denominatore if denominatore > 0 else None,
        'effettivi': lavaggi / k['giorni_persona'],
        'previsione': ParametriPrevisione.attuali().lavaggi_per_persona,
        'margine_obiettivo': margine_obiettivo,
    }


def persone_consigliate(righe, lavaggi_per_persona):
    """Aggiunge a ogni riga (medie dei giorni) le persone consigliate:
    lavaggi medi / lavaggi per persona al margine obiettivo, per eccesso."""
    for r in righe or []:
        r['consigliate'] = math.ceil(r['lavaggi'] / lavaggi_per_persona) if lavaggi_per_persona else None
    return righe


def produttivita(mesi, anno, base):
    from .models import ParametriAnalitica
    dal, al = mesi[0], fine_mese(mesi[-1])
    giorni = giorni_del_periodo(dal, al)
    p = pareggio(somma_kpi([kpi_mese(m, base) for m in mesi]), ParametriAnalitica.attuali().margine_obiettivo)
    obiettivo = p['obiettivo'] if p else None
    return {
        'andamento': andamento(anno, base),
        'pareggio': p,
        'settimana': persone_consigliate(per_giorno_settimana(giorni), obiettivo),
        'meteo': persone_consigliate(per_meteo(giorni, dal, al), obiettivo),
        'mix': mix_ed_extra(dal, min(al, timezone.localdate())),
        'giorni_aperti': len(giorni),
    }
