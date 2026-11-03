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
from datetime import date
from decimal import Decimal

from .conto_economico import (IVA, ZERO, differenze_abbinamento, fine_mese, quote_competenza, ricavi_canali,
                              rilevato_per_canale)
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


def raccogli(mesi, base='canali'):
    """Ricavi, costi per (centro, sezione, voce), ore del personale per
    centro e dati di volume del periodo. base come nel conto economico:
    'canali' = fatturato / 1,22; 'rilevato' = rilevato reale meno IVA,
    diviso sui canali in proporzione al fatturato di ogni mese."""
    from apps.personale.calcolo import calcola_mese
    from apps.personale.models import MesePersonale

    dal, al = mesi[0], fine_mese(mesi[-1])
    canali, lordi = ricavi_canali(mesi)
    if base == 'rilevato':
        canali = rilevato_per_canale(mesi, lordi)
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
    for m in differenze_abbinamento(dal, al):
        v = m.voce_differenza
        aggiungi(m.differenza if v.sezione == 'ricavi' else -m.differenza, v.ripartizione, v.sezione, v.nome)

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


def analitica(mesi, base='canali'):
    dati = raccogli(mesi, base)
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
        'rientro': rientro_investimenti(mesi, centri),
        'base': base,
        'avvisi': _avvisi(costi),
        'self': self_service, 'servito': servito, 'prodotti': prodotti, 'comune': centri['comune'],
        'quadro': quadro, 'centri': [n for _, n in CENTRI], 'ore': {NOMI_CENTRI[c]: v for c, v in ore.items()},
    }


def _anni_rientro(investito, flusso):
    return (investito / flusso).quantize(Decimal('0.1')) if investito > 0 and flusso > 0 else None


def _data_rientro(data_media, anni):
    if data_media is None or anni is None:
        return None
    from datetime import timedelta
    try:
        return data_media + timedelta(days=float(anni) * 365.25)
    except OverflowError:
        return None


def rientro_investimenti(mesi, centri):
    """Rientro dei cespiti registrati, per centro e in totale.

    Flusso annuo = flusso del periodo riportato a 12 mesi (sui mesi gia'
    iniziati):
    - prima delle imposte: risultato del centro + i suoi ammortamenti (che
      non sono uscite di cassa); in totale e' l'EBITDA;
    - dopo imposte e rate: meno oneri finanziari e rate dei finanziamenti
      (quota capitale, dai movimenti "Rimborso finanziamenti"), divisi sui
      centri in proporzione al valore dei cespiti, e meno le imposte,
      divise in proporzione al flusso positivo dei centri.
    Anni di rientro = valore dei cespiti / flusso annuo; la data stimata
    parte dalla data media di acquisto (pesata sul valore), supponendo lo
    stesso flusso anche negli anni passati."""
    from django.utils import timezone

    from .conto_economico import costi_per_voce
    from .models import VoceConto
    oggi = timezone.localdate()
    dal, al = mesi[0], fine_mese(mesi[-1])
    trascorsi = [m for m in mesi if m <= oggi]
    cespiti = [c for c in Cespite.objects.all() if c.data_acquisto <= al]
    if not trascorsi or not cespiti:
        return None
    fattore = Decimal(12) / len(trascorsi)

    investito, ammortizzato, giorni = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for c in cespiti:
        investito[c.centro] += c.valore
        ammortizzato[c.centro] += c.ammortizzato_al(al.replace(day=1))
        giorni[c.centro] += c.valore * c.data_acquisto.toordinal()

    per_voce = costi_per_voce(mesi)
    sezioni = dict(VoceConto.objects.filter(sezione__in=('imposte', 'oneri_finanziari')).values_list('pk', 'sezione'))
    imposte = sum((sum(per_voce.get(pk, []), ZERO) for pk, s in sezioni.items() if s == 'imposte'), ZERO) * fattore
    oneri = sum((sum(per_voce.get(pk, []), ZERO) for pk, s in sezioni.items() if s == 'oneri_finanziari'), ZERO) * fattore
    rate = -sum(MovimentoFinanziario.objects.filter(voce__codice='N.FINANZIAMENTI', importo__lt=0, data__gte=dal,
                                                     data__lte=al).values_list('importo', flat=True), ZERO) * fattore

    ante = {c: (centri[c]['ricavi'] - centri[c]['costi'] + centri[c]['totali']['ammortamenti']) * fattore
            for c, _ in CENTRI}
    tot_investito = sum(investito.values(), ZERO)
    positivi = sum((v for v in ante.values() if v > 0), ZERO)

    def riga(nome, centro_inv, centro_amm, giorni_pesati, flusso_ante, quota_fin, quota_imp):
        dopo = flusso_ante - quota_fin - quota_imp
        anni_ante, anni_dopo = _anni_rientro(centro_inv, flusso_ante), _anni_rientro(centro_inv, dopo)
        media = date.fromordinal(int(giorni_pesati / centro_inv)) if centro_inv else None
        return {'nome': nome, 'investito': centro_inv, 'ammortizzato': centro_amm, 'data_media': media,
                'ante': flusso_ante, 'finanziamenti': quota_fin, 'imposte': quota_imp, 'dopo': dopo,
                'anni_ante': anni_ante, 'anni_dopo': anni_dopo,
                'rientro_ante': _data_rientro(media, anni_ante), 'rientro_dopo': _data_rientro(media, anni_dopo),
                'roi_ante': _div(flusso_ante * 100, centro_inv), 'roi_dopo': _div(dopo * 100, centro_inv)}

    righe = []
    for c, nome in CENTRI:
        if not investito[c] and c == 'comune':
            continue
        quota_fin = (oneri + rate) * investito[c] / tot_investito if tot_investito else ZERO
        quota_imp = imposte * ante[c] / positivi if positivi and ante[c] > 0 else ZERO
        righe.append(riga(nome, investito[c], ammortizzato[c], giorni[c], ante[c], quota_fin, quota_imp))
    totale = riga('Totale azienda', tot_investito, sum(ammortizzato.values(), ZERO), sum(giorni.values(), ZERO),
                  sum(ante.values(), ZERO), oneri + rate, imposte)
    return {'righe': righe, 'totale': totale, 'mesi': len(trascorsi), 'imposte': imposte, 'oneri': oneri, 'rate': rate}


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
