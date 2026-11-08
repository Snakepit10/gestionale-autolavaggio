"""Capacita' del servito e costo dei servizi (time-driven: costo per
minuto di capacita' pagata, inattivita' a parte).

- Ore utili = somma (minuti-persona standard x quantita' venduta) / 60
  dei servizi negli ordini di lavaggio, accessi in abbonamento compresi.
- Saturazione = ore utili / ore pagate del servito.
- Costo orario della capacita' = (costo pieno - costi variabili) / ore
  pagate.
- Capacita' non usata = (ore pagate - ore utili) x costo orario.
- Costo di un servizio = consumi medi per servizio venduto + minuti
  standard / 60 x costo orario della capacita'.
Cosi' costo dei servizi venduti + capacita' non usata = costo pieno.

Minuti rilevati: dalle lavorazioni degli operatori chiuse. Per ogni ordine
e sessione gli intervalli si uniscono (chi sceglie piu' postazioni ha piu'
righe uguali), meno la pausa; poi si somma sulle sessioni. Contano solo
gli ordini con un solo servizio, per attribuirgli i minuti."""
import statistics
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .conto_economico import IVA, ZERO, fine_mese

MINUTI_MASSIMI = 600            # oltre, la rilevazione e' rimasta aperta: non conta
GIORNI_RILEVATI = 180


def _div(a, b):
    return a / b if b else None


def servizi_venduti(dal, al):
    """{servizio_id: {titolo, std, prezzo listino, pezzi, pezzi a zero euro, incasso}}
    delle righe di servizio negli ordini di lavaggio."""
    from apps.finanze.services.ricavi import ordini_di_lavaggio
    from apps.ordini.models import ItemOrdine, Ordine
    ordini = Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al).exclude(stato='annullato')
    ids = ordini_di_lavaggio(ordini).values('id')
    servizi = {}
    for sid, titolo, std, listino, prezzo, quantita in (
            ItemOrdine.objects.filter(ordine_id__in=ids, servizio_prodotto__tipo='servizio')
            .values_list('servizio_prodotto_id', 'servizio_prodotto__titolo',
                         'servizio_prodotto__minuti_persona_standard', 'servizio_prodotto__prezzo',
                         'prezzo_unitario', 'quantita')):
        s = servizi.setdefault(sid, {'id': sid, 'nome': titolo, 'std': std, 'listino': listino,
                                     'pezzi': 0, 'gratis': 0, 'incasso': ZERO})
        q = quantita or 1
        s['pezzi'] += q
        if prezzo:
            s['incasso'] += prezzo * q
        else:
            s['gratis'] += q
    return servizi


def capacita(mesi, ore_pagate, pieno, variabili, margine_obiettivo):
    """Ore utili, saturazione, capacita' non usata e tabella dei servizi."""
    dal, al = mesi[0], min(fine_mese(mesi[-1]), timezone.localdate())
    servizi = servizi_venduti(dal, al) if dal <= al else {}
    pezzi = sum(s['pezzi'] for s in servizi.values())
    pezzi_std = sum(s['pezzi'] for s in servizi.values() if s['std'] is not None)
    ore_utili = sum((Decimal(s['std'] * s['pezzi']) for s in servizi.values() if s['std'] is not None), ZERO) / 60
    costo_ora = _div(pieno - variabili, ore_pagate)
    consumi = _div(variabili, Decimal(pezzi))
    m = Decimal(margine_obiettivo) / 100
    righe = []
    for s in sorted(servizi.values(), key=lambda x: -x['pezzi']):
        pagati = s['pezzi'] - s['gratis']
        lavoro = (Decimal(s['std']) / 60 * costo_ora) if s['std'] is not None and costo_ora is not None else None
        costo = (lavoro + (consumi or ZERO)) if lavoro is not None else None
        ricavo = s['incasso'] / IVA / pagati if pagati else None
        righe.append({
            **s, 'ricavo': ricavo, 'consumi': consumi, 'lavoro': lavoro, 'costo': costo,
            'margine': ricavo - costo if ricavo is not None and costo is not None else None,
            'margine_pct': _div((ricavo - costo) * 100, ricavo) if ricavo is not None and costo is not None else None,
            'prezzo_obiettivo': costo / (1 - m) * IVA if costo is not None and m < 1 else None,
        })
    non_usata = (ore_pagate - ore_utili) * costo_ora if costo_ora is not None else None
    return {
        'ore_pagate': ore_pagate, 'ore_utili': ore_utili, 'saturazione': _div(ore_utili * 100, ore_pagate),
        'costo_ora': costo_ora, 'non_usata': non_usata, 'consumi': consumi,
        'pezzi': pezzi, 'pezzi_std': pezzi_std, 'copertura': _div(Decimal(pezzi_std) * 100, Decimal(pezzi)),
        'margine_obiettivo': margine_obiettivo, 'servizi': righe[:25],
    }


def _unisci(intervalli):
    """Durata in secondi dell'unione degli intervalli [(inizio, fine)]."""
    totale, fine_corrente, inizio_corrente = 0, None, None
    for inizio, fine in sorted(intervalli):
        if fine_corrente is None or inizio > fine_corrente:
            if fine_corrente is not None:
                totale += (fine_corrente - inizio_corrente).total_seconds()
            inizio_corrente, fine_corrente = inizio, fine
        else:
            fine_corrente = max(fine_corrente, fine)
    if fine_corrente is not None:
        totale += (fine_corrente - inizio_corrente).total_seconds()
    return totale


def minuti_per_ordine(dal, al):
    """{ordine_id: minuti-persona} dalle lavorazioni chiuse."""
    from apps.turni.models import LavorazioneOperatore
    per_sessione = defaultdict(lambda: {'intervalli': [], 'pausa': timedelta()})
    for ordine, sessione, inizio, fine, pausa in (
            LavorazioneOperatore.objects.filter(inizio__date__gte=dal, inizio__date__lte=al, fine__isnull=False,
                                                stato='completato')
            .values_list('ordine_id', 'sessione_id', 'inizio', 'fine', 'tempo_pausa_totale')):
        if fine <= inizio:
            continue
        s = per_sessione[(ordine, sessione)]
        s['intervalli'].append((inizio, fine))
        s['pausa'] = max(s['pausa'], pausa or timedelta())     # righe doppie: stessa pausa
    minuti = defaultdict(float)
    for (ordine, _), s in per_sessione.items():
        minuti[ordine] += max(_unisci(s['intervalli']) - s['pausa'].total_seconds(), 0) / 60
    return {o: m for o, m in minuti.items() if 0 < m <= MINUTI_MASSIMI}


def minuti_rilevati(oggi=None):
    """{servizio_id: (mediana minuti-persona, casi)} degli ultimi 180 giorni,
    dagli ordini con un solo servizio (una riga, quantita' 1)."""
    from apps.ordini.models import ItemOrdine
    oggi = oggi or timezone.localdate()
    minuti = minuti_per_ordine(oggi - timedelta(days=GIORNI_RILEVATI), oggi)
    if not minuti:
        return {}
    righe = defaultdict(list)
    for ordine, servizio, quantita in (ItemOrdine.objects.filter(ordine_id__in=list(minuti),
                                                                 servizio_prodotto__tipo='servizio')
                                       .values_list('ordine_id', 'servizio_prodotto_id', 'quantita')):
        righe[ordine].append((servizio, quantita))
    casi = defaultdict(list)
    for ordine, elenco in righe.items():
        if len(elenco) == 1 and elenco[0][1] == 1:
            casi[elenco[0][0]].append(minuti[ordine])
    return {s: (round(statistics.median(v)), len(v)) for s, v in casi.items()}
