"""Contabilita' analitica per centro: self-service, servito, vendita
prodotti e costi comuni.

Ogni costo del conto economico (fino all'EBIT) si divide sui centri:
- righe di fattura e movimenti: con la loro ripartizione o con quella
  della voce; senza ripartizione vanno sul Comune;
- personale: costo e ore di ogni dipendente con la sua ripartizione
  (senza ripartizione: tutto servito);
- ammortamenti: sul centro del cespite.

Self-service in **direct costing**: ricavi - costi variabili = margine
di contribuzione I; - costi fissi specifici = margine II, cioe' il
contributo alla copertura dei costi comuni (che non si ripartiscono).

Servito in **full costing**: ricavi - costi diretti - quota dei costi
comuni (in proporzione ai ricavi o con una percentuale fissa) =
risultato pieno; costo pieno per lavaggio, per ora e per servizio."""
from collections import defaultdict
from decimal import Decimal

from .conto_economico import IVA, ZERO, fine_mese, quote_competenza, ricavi_canali
from .models import (CENTRI, NOMI_CENTRI, Cespite, MovimentoFinanziario, ParametriAnalitica,
                     RigaFatturaPassiva, RipartizionePersonale)

SEZIONI_ANALITICA = [
    ('costi_variabili', 'Costi variabili'),
    ('costi_fissi', 'Costi fissi'),
    ('personale', 'Personale'),
    ('ammortamenti', 'Ammortamenti'),
]
CENTESIMO = Decimal('0.01')


def dividi(importo, ripartizione):
    """{centro: quota}; vuota = tutto sul comune; il resto
    dell'arrotondamento va sull'ultimo centro."""
    if not ripartizione:
        return {'comune': importo}
    centri = [(c, Decimal(str(ripartizione[c]))) for c, _ in CENTRI if ripartizione.get(c)]
    quote, assegnato = {}, ZERO
    for i, (centro, pct) in enumerate(centri):
        if i == len(centri) - 1:
            quote[centro] = importo - assegnato
        else:
            quote[centro] = (importo * pct / 100).quantize(CENTESIMO)
            assegnato += quote[centro]
    return quote


def ripartizione_dipendente(dipendente_id, mese, ripartizioni):
    for r in ripartizioni.get(dipendente_id, []):          # dal piu' recente
        if r.valido_dal <= mese:
            return r.ripartizione
    return {'servito': 100}


def raccogli(mesi):
    """Ricavi, costi per (centro, sezione, voce), ore del personale per
    centro e dati di volume del periodo."""
    from apps.personale.calcolo import calcola_mese
    from apps.personale.models import MesePersonale

    dal, al = mesi[0], fine_mese(mesi[-1])
    canali, lordi = ricavi_canali(mesi)
    ricavi = defaultdict(lambda: ZERO)
    ricavi['self'] = sum(canali['portali'] + canali['cambia_gettoni'] + canali['monete_online'], ZERO)
    ricavi['servito'] = sum(canali['servito'], ZERO)
    ricavi['prodotti'] = sum(canali['prodotti'], ZERO)
    costi = defaultdict(lambda: ZERO)              # (centro, sezione, etichetta) -> importo

    def aggiungi(importo, ripartizione, sezione, etichetta):
        if sezione == 'ricavi':
            for centro, quota in dividi(importo, ripartizione).items():
                ricavi[centro] += quota
        elif sezione in dict(SEZIONI_ANALITICA):
            for centro, quota in dividi(importo, ripartizione).items():
                costi[(centro, sezione, etichetta)] += quota

    righe = (RigaFatturaPassiva.objects.filter(fattura__competenza_al__gte=dal, fattura__competenza_dal__lte=mesi[-1])
             .select_related('fattura', 'voce'))
    for r in righe:
        if r.voce and not r.voce.economica:
            continue
        sezione = r.voce.sezione if r.voce else 'costi_fissi'
        etichetta = r.voce.nome if r.voce else 'Fatture da classificare'
        importo = sum((q for m, q in quote_competenza(r.imponibile, r.fattura.competenza_dal, r.fattura.competenza_al).items()
                       if dal <= m <= al), ZERO)
        if importo:
            aggiungi(-importo if sezione == 'ricavi' else importo, r.ripartizione_effettiva, sezione, etichetta)
    movimenti = (MovimentoFinanziario.objects.filter(data__gte=dal, data__lte=al, voce__isnull=False, scadenze__isnull=True)
                 .exclude(voce__sezione='non_economico').select_related('voce'))
    for m in movimenti:
        importo = m.importo if m.voce.sezione == 'ricavi' else -m.importo
        aggiungi(importo, m.ripartizione_effettiva, m.voce.sezione, m.voce.nome)

    ripartizioni = defaultdict(list)
    for r in RipartizionePersonale.objects.order_by('-valido_dal'):
        ripartizioni[r.dipendente_id].append(r)
    ore = defaultdict(lambda: ZERO)
    for mese_obj in MesePersonale.objects.filter(mese__in=mesi):
        for riga in calcola_mese(mese_obj)['righe']:
            rip = ripartizione_dipendente(riga['dipendente'].pk, mese_obj.mese, ripartizioni)
            aggiungi(riga['costo'], rip, 'personale', 'Costo aziendale dei dipendenti')
            for centro, quota in dividi(riga['ore_lavorate'], rip).items():
                ore[centro] += quota
    for c in Cespite.objects.all():
        quota = sum((c.quota(m) for m in mesi), ZERO)
        if quota:
            aggiungi(quota, {c.centro: 100}, 'ammortamenti', f'Ammortamento {c.descrizione}')

    return {
        'ricavi': ricavi,
        'costi': costi,
        'ore': ore,
        'washcycles': sum((r['washcycles'] for r in lordi), 0),
        'ordini_lavaggio': sum((r['ordini_lavaggio'] for r in lordi), 0),
        'ricavi_portali': sum(canali['portali'], ZERO),
    }


def _per_sezione(costi, centro):
    """[{chiave, nome, voci: [(etichetta, importo)], totale}] del centro."""
    sezioni = []
    for chiave, nome in SEZIONI_ANALITICA:
        voci = sorted(((et, v) for (c, s, et), v in costi.items() if c == centro and s == chiave and v),
                      key=lambda x: -x[1])
        sezioni.append({'chiave': chiave, 'nome': nome, 'voci': voci, 'totale': sum((v for _, v in voci), ZERO)})
    return sezioni


def _div(a, b):
    return a / b if b else None


def analitica(mesi):
    dati = raccogli(mesi)
    ricavi, costi, ore = dati['ricavi'], dati['costi'], dati['ore']
    centri = {}
    for centro, nome in CENTRI:
        sezioni = _per_sezione(costi, centro)
        tot = {s['chiave']: s['totale'] for s in sezioni}
        centri[centro] = {'nome': nome, 'ricavi': ricavi[centro], 'sezioni': sezioni, 'totali': tot,
                          'costi': sum(tot.values(), ZERO)}

    # Self-service: direct costing
    s = centri['self']
    variabili = s['totali']['costi_variabili']
    fissi_specifici = s['costi'] - variabili
    mdc1 = s['ricavi'] - variabili
    cicli = dati['washcycles']
    mdc_ciclo = _div(mdc1, cicli)
    self_service = {
        'ricavi': s['ricavi'], 'variabili': variabili, 'mdc1': mdc1, 'mdc1_pct': _div(mdc1 * 100, s['ricavi']),
        'fissi_specifici': fissi_specifici, 'mdc2': mdc1 - fissi_specifici,
        'mdc2_pct': _div((mdc1 - fissi_specifici) * 100, s['ricavi']),
        'cicli': cicli,
        'ricavo_ciclo': _div(dati['ricavi_portali'], cicli),
        'variabile_ciclo': _div(variabili, cicli),
        'mdc_ciclo': mdc_ciclo,
        'cicli_pareggio': (fissi_specifici / mdc_ciclo).quantize(Decimal('1')) if mdc_ciclo and mdc_ciclo > 0 else None,
        'sezioni': s['sezioni'],
    }

    # Servito: full costing
    parametri = ParametriAnalitica.attuali()
    ricavi_totali = sum((c['ricavi'] for c in centri.values()), ZERO)
    if parametri.criterio_comuni == 'percentuale':
        quota_pct = parametri.percentuale_servito
    else:
        quota_pct = (centri['servito']['ricavi'] / ricavi_totali * 100) if ricavi_totali else ZERO
    comuni = centri['comune']['costi']
    quota_comuni = (comuni * quota_pct / 100).quantize(CENTESIMO)
    sv = centri['servito']
    diretti = sv['costi']
    pieno = diretti + quota_comuni
    lavaggi = dati['ordini_lavaggio']
    ore_servito = ore['servito']
    costo_ora = _div(pieno, ore_servito)
    servito = {
        'ricavi': sv['ricavi'], 'diretti': diretti, 'margine_diretto': sv['ricavi'] - diretti,
        'comuni': comuni, 'quota_pct': quota_pct, 'quota_comuni': quota_comuni, 'criterio': parametri.get_criterio_comuni_display(),
        'pieno': pieno, 'risultato': sv['ricavi'] - pieno, 'risultato_pct': _div((sv['ricavi'] - pieno) * 100, sv['ricavi']),
        'lavaggi': lavaggi, 'costo_lavaggio': _div(pieno, lavaggi), 'ricavo_lavaggio': _div(sv['ricavi'], lavaggi),
        'ore': ore_servito, 'costo_ora': costo_ora, 'ricavo_ora': _div(sv['ricavi'], ore_servito),
        'sezioni': sv['sezioni'],
        'servizi': costo_per_servizio(mesi, costo_ora),
    }

    p = centri['prodotti']
    prodotti = {'ricavi': p['ricavi'], 'costi': p['costi'], 'margine': p['ricavi'] - p['costi'],
                'margine_pct': _div((p['ricavi'] - p['costi']) * 100, p['ricavi']), 'sezioni': p['sezioni']}

    # Quadro per centro (fino all'EBIT): la somma torna col conto economico
    righe = [('Ricavi', {c: centri[c]['ricavi'] for c, _ in CENTRI})]
    for chiave, nome in SEZIONI_ANALITICA:
        righe.append((nome, {c: centri[c]['totali'][chiave] for c, _ in CENTRI}))
    righe.append(('Risultato del centro', {c: centri[c]['ricavi'] - centri[c]['costi'] for c, _ in CENTRI}))
    quadro = [{'nome': n, 'valori': [v[c] for c, _ in CENTRI], 'totale': sum(v.values(), ZERO)} for n, v in righe]
    return {
        'avvisi': _avvisi(costi),
        'self': self_service, 'servito': servito, 'prodotti': prodotti, 'comune': centri['comune'],
        'quadro': quadro, 'centri': [n for _, n in CENTRI], 'ore': {NOMI_CENTRI[c]: v for c, v in ore.items()},
    }


def costo_per_servizio(mesi, costo_ora):
    """Servizi venduti nel periodo: ricavo netto medio contro costo pieno
    stimato (costo orario pieno x durata del servizio)."""
    from django.db.models import F, Sum

    from apps.ordini.models import ItemOrdine
    if not costo_ora:
        return []
    righe = (ItemOrdine.objects
             .filter(ordine__data_ora__date__gte=mesi[0], ordine__data_ora__date__lte=fine_mese(mesi[-1]),
                     servizio_prodotto__tipo='servizio')
             .exclude(ordine__stato='annullato')
             .values('servizio_prodotto__titolo', 'servizio_prodotto__durata_minuti')
             .annotate(pezzi=Sum('quantita'), incasso=Sum(F('prezzo_unitario') * F('quantita')))
             .order_by('-incasso'))
    servizi = []
    for r in righe[:20]:
        quantita = r['pezzi'] or 0
        if not quantita:
            continue
        ricavo = (Decimal(r['incasso'] or 0) / IVA / quantita).quantize(CENTESIMO)
        durata = r['servizio_prodotto__durata_minuti'] or 0
        costo = (costo_ora * durata / 60).quantize(CENTESIMO) if durata else None
        servizi.append({'nome': r['servizio_prodotto__titolo'], 'quantita': quantita, 'durata': durata,
                        'ricavo': ricavo, 'costo': costo,
                        'margine': ricavo - costo if costo is not None else None,
                        'margine_pct': _div((ricavo - costo) * 100, ricavo) if costo is not None else None})
    return servizi


def _avvisi(costi):
    """Ripartizioni che mancano."""
    from apps.personale.models import Dipendente
    avvisi = []
    senza = Dipendente.objects.filter(attivo=True, ripartizioni_centri__isnull=True).count()
    if senza:
        avvisi.append(f'{senza} dipendenti senza ripartizione: considerati 100% servito')
    voci = sorted({et for (centro, _, et), v in costi.items() if centro == 'comune' and v})
    if voci:
        avvisi.append('Costi sul centro Comune (senza ripartizione): ' + ', '.join(voci[:12])
                      + (' e altri' if len(voci) > 12 else ''))
    return avvisi
