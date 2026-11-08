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
comuni = risultato pieno; costo pieno per lavaggio, per ora e per servizio.

Costi comuni: ognuno va sui centri con il driver della sua voce (o del
cespite): ricavi, cicli dei portali (self contro servito: e' il prezzo
interno del ciclo) oppure ore del personale. Il personale sul Comune va
per ore, le fatture da classificare per ricavi. Tutti i centri arrivano
cosi' allo stesso risultato pieno e la somma e' l'EBIT."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .conto_economico import (IVA, ZERO, differenze_abbinamento, fine_mese, quote_competenza, ricavi_canali,
                              rilevato_per_canale, scontrini_per_canale, costo_dipendente, NOMI_PERSONALE)
from .models import (CENTRI, DRIVER_COMUNI, NOMI_CENTRI, Cespite, MovimentoFinanziario, RigaFatturaPassiva,
                     RipartizionePersonale)

SEZIONI_ANALITICA = [
    ('costi_variabili', 'Costi variabili'),
    ('costi_fissi', 'Costi fissi'),
    ('personale', 'Personale'),
    ('ammortamenti', 'Ammortamenti'),
]
CENTESIMO = Decimal('0.01')
CENTRI_OPERATIVI = ('self', 'servito', 'prodotti')
NOMI_DRIVER = dict(DRIVER_COMUNI)


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


def raccogli(mesi, base='rilevato'):
    """Ricavi, costi per (centro, sezione, voce), ore del personale per
    centro e dati di volume del periodo. base come nel conto economico:
    'rilevato' = ordini meno IVA del registratore,
    cambia gettoni / 1,22, portali = resto del rilevato meno la loro IVA;
    'scontrini' = registratore e casse automatiche / 1,22."""
    from apps.personale.calcolo import calcola_mese
    from apps.personale.models import MesePersonale

    dal, al = mesi[0], fine_mese(mesi[-1])
    _, lordi = ricavi_canali(mesi)
    canali = scontrini_per_canale(lordi) if base == 'scontrini' else rilevato_per_canale(mesi, lordi)
    ricavi = defaultdict(lambda: ZERO)
    ricavi['self'] = sum(canali['portali'] + canali['cambia_gettoni'] + canali['monete_online'], ZERO)
    ricavi['servito'] = sum(canali['servito'], ZERO)
    ricavi['prodotti'] = sum(canali['prodotti'], ZERO)
    costi = defaultdict(lambda: ZERO)              # (centro, sezione, etichetta) -> importo
    driver = {}                                    # (sezione, etichetta) -> driver se resta sul comune

    def aggiungi(importo, ripartizione, sezione, etichetta, driver_comuni='ricavi'):
        if sezione == 'ricavi':
            for centro, quota in dividi(importo, ripartizione).items():
                ricavi[centro] += quota
        elif sezione in dict(SEZIONI_ANALITICA):
            for centro, quota in dividi(importo, ripartizione).items():
                costi[(centro, sezione, etichetta)] += quota
                if centro == 'comune':
                    driver[(sezione, etichetta)] = driver_comuni

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
            aggiungi(-importo if sezione == 'ricavi' else importo, r.ripartizione_effettiva, sezione, etichetta,
                     r.voce.driver_comuni if r.voce else 'ricavi')
    movimenti = (MovimentoFinanziario.objects.filter(data__gte=dal, data__lte=al, voce__isnull=False, scadenze__isnull=True)
                 .exclude(voce__sezione='non_economico').select_related('voce'))
    for m in movimenti:
        importo = m.importo if m.voce.sezione == 'ricavi' else -m.importo
        aggiungi(importo, m.ripartizione_effettiva, m.voce.sezione, m.voce.nome, m.voce.driver_comuni)
    for m in differenze_abbinamento(dal, al):
        v = m.voce_differenza
        aggiungi(m.differenza if v.sezione == 'ricavi' else -m.differenza, v.ripartizione, v.sezione, v.nome,
                 v.driver_comuni)

    ripartizioni = defaultdict(list)
    for r in RipartizionePersonale.objects.order_by('-valido_dal'):
        ripartizioni[r.dipendente_id].append(r)
    ore = defaultdict(lambda: ZERO)
    for mese_obj in MesePersonale.objects.filter(mese__in=mesi):
        for riga in calcola_mese(mese_obj)['righe']:
            rip = ripartizione_dipendente(riga['dipendente'].pk, mese_obj.mese, ripartizioni)
            aggiungi(costo_dipendente(riga, base), rip, 'personale', NOMI_PERSONALE.get(base, NOMI_PERSONALE['rilevato']),
                     'ore')
            for centro, quota in dividi(riga['ore_lavorate'], rip).items():
                ore[centro] += quota
    for c in Cespite.objects.all():
        quota = sum((c.quota(m) for m in mesi), ZERO)
        if quota:
            aggiungi(quota, {c.centro: 100}, 'ammortamenti', f'Ammortamento {c.descrizione}', c.driver_comuni)

    return {
        'ricavi': ricavi,
        'costi': costi,
        'driver': driver,
        'ore': ore,
        'washcycles': sum((r['washcycles'] for r in lordi), 0),
        'cicli_servito': sum((r.get('cicli_servito', 0) for r in lordi), 0),
        'ordini_lavaggio': sum((r['ordini_lavaggio'] for r in lordi), 0),
        'lavaggi_abbonamento': sum((r.get('lavaggi_abbonamento', 0) for r in lordi), 0),
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


def ripartisci(importo, pesi):
    """{centro: quota} in proporzione ai pesi positivi; il resto
    dell'arrotondamento sull'ultimo. Pesi tutti nulli: {}."""
    validi = [(c, Decimal(str(p))) for c, p in pesi.items() if p and p > 0]
    totale = sum((p for _, p in validi), ZERO)
    if not totale:
        return {}
    quote, assegnato = {}, ZERO
    for i, (centro, peso) in enumerate(validi):
        quote[centro] = importo - assegnato if i == len(validi) - 1 else (importo * peso / totale).quantize(CENTESIMO)
        assegnato += quote[centro]
    return quote


def dividi_comuni(costi, driver, ricavi, ore, cicli):
    """Costi del centro Comune divisi sui centri con il driver di ogni voce.
    cicli = {'self': n, 'servito': n}. Se un driver non ha base (es.
    nessun ciclo) si usano i ricavi. Restituisce (quote per centro,
    dettaglio per voce)."""
    pesi = {
        'ricavi': {c: ricavi[c] for c in CENTRI_OPERATIVI},
        'cicli': dict(cicli),
        'ore': {c: ore[c] for c in CENTRI_OPERATIVI},
    }
    quote = defaultdict(lambda: ZERO)
    dettaglio = []
    for (centro, sezione, etichetta), importo in sorted(costi.items(), key=lambda x: -x[1]):
        if centro != 'comune' or not importo:
            continue
        criterio = driver.get((sezione, etichetta), 'ricavi')
        parti = ripartisci(importo, pesi[criterio])
        if not parti and criterio != 'ricavi':
            criterio = 'ricavi'
            parti = ripartisci(importo, pesi['ricavi'])
        for c, q in parti.items():
            quote[c] += q
        dettaglio.append({'nome': etichetta, 'driver': criterio, 'nome_driver': NOMI_DRIVER[criterio], 'importo': importo,
                          'quote': [parti.get(c, ZERO) for c in CENTRI_OPERATIVI]})
    return quote, dettaglio


def _centri(ricavi, costi):
    centri = {}
    for centro, nome in CENTRI:
        sezioni = _per_sezione(costi, centro)
        tot = {s['chiave']: s['totale'] for s in sezioni}
        centri[centro] = {'nome': nome, 'ricavi': ricavi[centro], 'sezioni': sezioni, 'totali': tot,
                          'costi': sum(tot.values(), ZERO)}
    return centri


def calcola_centri(mesi, base='rilevato'):
    """Raccolta del periodo, centri con i loro margini e costi comuni divisi
    con i driver. Lo usano la scheda Centri e la Produttivita'."""
    dati = raccogli(mesi, base)
    centri = _centri(dati['ricavi'], dati['costi'])
    # Il contatore dei portali conta anche i cicli fatti per il servito:
    # al self restano i cicli totali meno quelli del servito
    cicli_totali = dati['washcycles']
    cicli_servito = min(dati['cicli_servito'], cicli_totali)
    cicli = cicli_totali - cicli_servito
    quote, dettaglio = dividi_comuni(dati['costi'], dati['driver'], dati['ricavi'], dati['ore'],
                                     {'self': cicli, 'servito': cicli_servito})
    for c, centro in centri.items():
        centro['mdc'] = centro['ricavi'] - centro['totali']['costi_variabili']
        centro['margine'] = centro['ricavi'] - centro['costi']
        centro['quota_comuni'] = quote[c] if c != 'comune' else -sum(quote.values(), ZERO)
        centro['pieno'] = centro['margine'] - centro['quota_comuni']
    return {'dati': dati, 'centri': centri, 'quote': quote, 'dettaglio_comuni': dettaglio,
            'cicli': cicli, 'cicli_servito': cicli_servito, 'cicli_totali': cicli_totali}


def analitica(mesi, base='rilevato'):
    from django.utils import timezone
    calcolo = calcola_centri(mesi, base)
    dati, centri, quote, dettaglio_comuni = calcolo['dati'], calcolo['centri'], calcolo['quote'], calcolo['dettaglio_comuni']
    ore = dati['ore']
    cicli, cicli_servito, cicli_totali = calcolo['cicli'], calcolo['cicli_servito'], calcolo['cicli_totali']
    # il rientro si porta a 12 mesi: solo i mesi gia' iniziati, senza la competenza di quelli futuri
    trascorsi = [m for m in mesi if m <= timezone.localdate()]
    if trascorsi and len(trascorsi) < len(mesi):
        passati = raccogli(trascorsi, base)
        centri_rientro = _centri(passati['ricavi'], passati['costi'])
    else:
        centri_rientro = centri
    comuni = centri['comune']['costi']
    costi_cicli = sum((d['importo'] for d in dettaglio_comuni if d['driver'] == 'cicli'), ZERO)
    costo_ciclo_portale = _div(costi_cicli, cicli_totali)

    # Self-service: direct costing, piu' il risultato pieno
    s = centri['self']
    variabili = s['totali']['costi_variabili']
    fissi_specifici = s['costi'] - variabili
    mdc1 = s['mdc']
    mdc_ciclo = _div(mdc1, cicli)
    self_service = {
        'ricavi': s['ricavi'], 'variabili': variabili, 'mdc1': mdc1, 'mdc1_pct': _div(mdc1 * 100, s['ricavi']),
        'fissi_specifici': fissi_specifici, 'mdc2': mdc1 - fissi_specifici,
        'mdc2_pct': _div((mdc1 - fissi_specifici) * 100, s['ricavi']),
        'quota_comuni': s['quota_comuni'], 'risultato': s['pieno'], 'risultato_pct': _div(s['pieno'] * 100, s['ricavi']),
        'cicli': cicli, 'cicli_totali': cicli_totali, 'cicli_servito': cicli_servito,
        'ricavo_ciclo': _div(dati['ricavi_portali'], cicli),
        'variabile_ciclo': _div(variabili, cicli),
        'mdc_ciclo': mdc_ciclo,
        'cicli_pareggio': (fissi_specifici / mdc_ciclo).quantize(Decimal('1')) if mdc_ciclo and mdc_ciclo > 0 else None,
        'costo_ciclo_portale': costo_ciclo_portale,
        'sezioni': s['sezioni'],
    }

    # Servito: full costing
    sv = centri['servito']
    diretti = sv['costi']
    quota_comuni = sv['quota_comuni']
    pieno = diretti + quota_comuni
    lavaggi = dati['ordini_lavaggio']
    ore_servito = ore['servito']
    costo_ora = _div(pieno, ore_servito)
    servito = {
        'ricavi': sv['ricavi'], 'variabili': sv['totali']['costi_variabili'], 'mdc': sv['mdc'],
        'diretti': diretti, 'margine_diretto': sv['margine'],
        'comuni': comuni, 'quota_pct': _div(quota_comuni * 100, comuni) or ZERO, 'quota_comuni': quota_comuni,
        'pieno': pieno, 'risultato': sv['pieno'], 'risultato_pct': _div(sv['pieno'] * 100, sv['ricavi']),
        'lavaggi': lavaggi, 'lavaggi_abbonamento': dati['lavaggi_abbonamento'],
        'costo_lavaggio': _div(pieno, lavaggi), 'ricavo_lavaggio': _div(sv['ricavi'], lavaggi),
        'ore': ore_servito, 'costo_ora': costo_ora, 'ricavo_ora': _div(sv['ricavi'], ore_servito),
        'cicli_portale': cicli_servito, 'costo_cicli_portale': quote_driver(dettaglio_comuni, 'cicli', 'servito'),
        'sezioni': sv['sezioni'],
        'servizi': costo_per_servizio(mesi, costo_ora),
    }

    p = centri['prodotti']
    prodotti = {'ricavi': p['ricavi'], 'costi': p['costi'], 'margine': p['margine'],
                'margine_pct': _div(p['margine'] * 100, p['ricavi']), 'quota_comuni': p['quota_comuni'],
                'risultato': p['pieno'], 'risultato_pct': _div(p['pieno'] * 100, p['ricavi']), 'sezioni': p['sezioni']}

    # Quadro per centro (fino all'EBIT): la somma torna col conto economico
    sezioni = dict(SEZIONI_ANALITICA)
    righe = [
        ('Ricavi', 'riga-totale', 'ricavi'),
        (sezioni['costi_variabili'], '', 'costi_variabili'),
        ('Margine di contribuzione', 'riga-totale', 'mdc'),
        (sezioni['costi_fissi'], '', 'costi_fissi'),
        (sezioni['personale'], '', 'personale'),
        (sezioni['ammortamenti'], '', 'ammortamenti'),
        ('Margine del centro', 'riga-totale', 'margine'),
        ('Quota costi comuni', '', 'quota_comuni'),
        ('Risultato pieno', 'riga-risultato', 'pieno'),
    ]
    quadro = []
    for nome, classe, chiave in righe:
        valori = [centri[c][chiave] if chiave in centri[c] else centri[c]['totali'][chiave] for c, _ in CENTRI]
        quadro.append({'nome': nome, 'classe': classe, 'valori': valori, 'totale': sum(valori, ZERO)})
    return {
        'rientro': rientro_investimenti(trascorsi or mesi, centri_rientro),
        'base': base,
        'avvisi': _avvisi(),
        'self': self_service, 'servito': servito, 'prodotti': prodotti, 'comune': centri['comune'],
        'comuni': {'totale': comuni, 'dettaglio': dettaglio_comuni, 'centri': [NOMI_CENTRI[c] for c in CENTRI_OPERATIVI],
                   'quote': [quote[c] for c in CENTRI_OPERATIVI]},
        'quadro': quadro, 'centri': [n for _, n in CENTRI], 'ore': {NOMI_CENTRI[c]: v for c, v in ore.items()},
    }


def quote_driver(dettaglio, driver, centro):
    """Parte dei costi comuni con quel driver andata al centro."""
    i = CENTRI_OPERATIVI.index(centro)
    return sum((d['quote'][i] for d in dettaglio if d['driver'] == driver), ZERO)


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


def _avvisi():
    """Ripartizioni che mancano."""
    from apps.personale.models import Dipendente
    avvisi = []
    senza = Dipendente.objects.filter(attivo=True, ripartizioni_centri__isnull=True).count()
    if senza:
        avvisi.append(f'{senza} dipendenti senza ripartizione: considerati 100% servito')
    return avvisi
