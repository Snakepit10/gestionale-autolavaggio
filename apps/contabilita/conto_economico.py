"""Conto economico (per competenza) e rendiconto dei flussi di cassa (per
cassa), mese per mese. Funzioni pure sui dati del gestionale; tutto in
Decimal, arrotondamenti solo a video.

Conto economico (al netto IVA, regime ordinario):
- ricavi: dalle chiusure e dagli ordini (ricavi_periodo) / 1,22, piu' i
  movimenti classificati su voci di ricavo;
- costi: righe delle fatture dei fornitori ripartite sui mesi di
  competenza; movimenti di banca e cassa su voci economiche che non
  pagano fatture (commissioni, interessi, spese di cassa...); costo
  aziendale del personale; ammortamenti dei cespiti.
I pagamenti delle fatture, gli incassi POS e i giroconti non sono
ricavi ne' costi: contano solo nei flussi di cassa."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from .models import (Cespite, ContoFinanziario, FatturaPassiva, MovimentoFinanziario, RigaFatturaPassiva,
                     VoceConto)

ZERO = Decimal('0')
IVA = Decimal('1.22')
NOMI_MESI = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
CANALI = [
    ('servito', 'Servito (lavaggi)'),
    ('prodotti', 'Vendita prodotti'),
    ('portali', 'Portali self-service'),
    ('cambia_gettoni', 'Cambia gettoni e piste'),
    ('monete_online', 'Monete online (app)'),
]


def mese_successivo(mese):
    return (mese.replace(day=28) + timedelta(days=4)).replace(day=1)


def fine_mese(mese):
    return mese_successivo(mese) - timedelta(days=1)


def mesi_tra(dal, al):
    mesi, m = [], dal.replace(day=1)
    while m <= al:
        mesi.append(m)
        m = mese_successivo(m)
    return mesi


def quote_competenza(importo, dal, al):
    """{mese: quota} in parti uguali; l'ultima prende il resto."""
    mesi = mesi_tra(dal, al)
    quota = (importo / len(mesi)).quantize(Decimal('0.01'))
    quote = {m: quota for m in mesi}
    quote[mesi[-1]] = importo - quota * (len(mesi) - 1)
    return quote


# ---------------------------------------------------------------- raccolta

def costi_per_voce(mesi):
    """{chiave: [importo per mese]}: chiave = id della voce, 'dacl' per le
    righe di fattura senza voce. Costi positivi, ricavi (voci di ricavo)
    positivi."""
    indice = {m: i for i, m in enumerate(mesi)}
    valori = defaultdict(lambda: [ZERO] * len(mesi))
    righe = (RigaFatturaPassiva.objects
             .filter(fattura__competenza_al__gte=mesi[0], fattura__competenza_dal__lte=mesi[-1])
             .select_related('fattura', 'voce'))
    for r in righe:
        if r.voce and not r.voce.economica:
            continue                                  # es. acquisto di un impianto: va nei cespiti
        chiave = r.voce_id or 'dacl'
        segno = -1 if r.voce and r.voce.sezione == 'ricavi' else 1
        for mese, quota in quote_competenza(r.imponibile, r.fattura.competenza_dal, r.fattura.competenza_al).items():
            if mese in indice:
                valori[chiave][indice[mese]] += quota * segno
    movimenti = (MovimentoFinanziario.objects
                 .filter(data__gte=mesi[0], data__lte=fine_mese(mesi[-1]), voce__isnull=False, scadenze__isnull=True)
                 .exclude(voce__sezione='non_economico').select_related('voce'))
    for m in movimenti:
        i = indice[m.data.replace(day=1)]
        # un'uscita e' un costo positivo; su una voce di ricavo l'entrata e' ricavo positivo
        valori[m.voce_id][i] += m.importo if m.voce.sezione == 'ricavi' else -m.importo
    # pagamenti di fatture con una differenza (es. la commissione del bonifico)
    for m in differenze_abbinamento(mesi[0], fine_mese(mesi[-1])):
        i = indice[m.data.replace(day=1)]
        valori[m.voce_differenza_id][i] += m.differenza if m.voce_differenza.sezione == 'ricavi' else -m.differenza
    return valori


def differenze_abbinamento(dal, al):
    """Movimenti abbinati a fatture la cui differenza d'importo e' un costo
    o un ricavo (la voce del movimento resta "Pagamento fornitori")."""
    return (MovimentoFinanziario.objects.filter(data__gte=dal, data__lte=al, voce_differenza__isnull=False)
            .exclude(differenza=0).select_related('voce_differenza'))


def ricavi_canali(mesi):
    from apps.finanze.services.ricavi import ricavi_periodo
    per_mese = [ricavi_periodo(m, fine_mese(m)) for m in mesi]
    return {k: [(r[k] / IVA).quantize(Decimal('0.01')) for r in per_mese] for k, _ in CANALI}, per_mese


def ricavi_rilevati(mesi):
    """Rilevato reale per mese, come nel report periodo: nei giorni con
    quadratura contanti + POS - fondo + spese di cassa + ticket di resto.
    Restituisce anche i giorni con quadratura."""
    from collections import Counter

    from apps.finanze.models import QuadraturaGiornaliera, SpesaCassa, TicketResto
    dal, al = mesi[0], fine_mese(mesi[-1])
    indice = {m: i for i, m in enumerate(mesi)}
    spese, ticket = Counter(), Counter()
    for s in SpesaCassa.objects.filter(data__gte=dal, data__lte=al).values('data', 'importo'):
        spese[s['data']] += s['importo']
    for t in TicketResto.objects.filter(data__gte=dal, data__lte=al).values('data', 'importo'):
        ticket[t['data']] += t['importo']
    rilevato, giorni = [ZERO] * len(mesi), [0] * len(mesi)
    for q in QuadraturaGiornaliera.objects.filter(data__gte=dal, data__lte=al):
        i = indice[q.data.replace(day=1)]
        rilevato[i] += q.totale_reale + spese[q.data] + ticket[q.data]
        giorni[i] += 1
    return rilevato, giorni


def _scorporo(lordo):
    """IVA al 22% contenuta in un importo lordo."""
    return lordo - (lordo / IVA).quantize(Decimal('0.01')) if lordo > 0 else ZERO


def rilevato_per_canale(mesi, ricavi_lordi):
    """Il rilevato reale di ogni mese sui canali, IVA esclusa:
    - servito e vendita prodotti: il totale degli ordini meno l'IVA del
      registratore fiscale, divisa fra i due in proporzione al fatturato;
    - cambia gettoni: il totale delle chiusure di cassa / 1,22;
    - monete online: il loro fatturato / 1,22 (non passano dalla cassa);
    - portali (spazzole): il resto del rilevato, tolti gli ordini e il
      cambia gettoni, meno l'IVA delle chiusure di cassa dei portali.
    Stessa forma di ricavi_canali, piu' 'iva_registratore', 'iva_portali'
    e 'rilevato'."""
    rilevato, _ = ricavi_rilevati(mesi)
    diviso = {k: [(lordi[k] / IVA).quantize(Decimal('0.01')) for lordi in ricavi_lordi] for k, _ in CANALI}
    diviso['iva_registratore'], diviso['iva_portali'], diviso['rilevato'] = [ZERO] * len(mesi), [ZERO] * len(mesi), rilevato
    for i, lordi in enumerate(ricavi_lordi):
        iva_reg = _scorporo(lordi['registratore'])
        ordini = lordi['servito'] + lordi['prodotti']
        quota_prodotti = (iva_reg * lordi['prodotti'] / ordini).quantize(Decimal('0.01')) if ordini > 0 else ZERO
        diviso['servito'][i] = lordi['servito'] - (iva_reg - quota_prodotti)
        diviso['prodotti'][i] = lordi['prodotti'] - quota_prodotti
        iva_portali = _scorporo(lordi['portali'])
        diviso['portali'][i] = rilevato[i] - ordini - lordi['cambia_gettoni'] - iva_portali
        diviso['iva_registratore'][i], diviso['iva_portali'][i] = iva_reg, iva_portali
    return diviso


def costo_personale(mesi):
    from apps.personale.calcolo import calcola_mese
    from apps.personale.models import MesePersonale
    presenti = {m.mese: m for m in MesePersonale.objects.filter(mese__in=mesi)}
    return [calcola_mese(presenti[m])['riepilogo']['costo'] if m in presenti else ZERO for m in mesi], presenti


def ammortamenti(mesi):
    cespiti = list(Cespite.objects.all())
    return [sum((c.quota(m) for c in cespiti), ZERO) for m in mesi]


# ---------------------------------------------------------------- conto economico

def _riga(tipo, nome, valori, chiave='', livello=0):
    return {'tipo': tipo, 'nome': nome, 'valori': list(valori), 'totale': sum(valori, ZERO),
            'chiave': chiave, 'livello': livello}


def _somma(*liste):
    return [sum(v, ZERO) for v in zip(*liste)]


def _differenza(a, b):
    return [x - y for x, y in zip(a, b)]


BASI_RICAVI = {
    'rilevato': 'Rilevato reale meno IVA dei corrispettivi',
    'canali': 'Fatturato per canale, IVA esclusa',
}


def conto_economico(mesi, base='canali'):
    """base 'canali': ricavi per canale dal fatturato (servito ordinato e
    vendite self service) / 1,22; base 'rilevato': soldi effettivamente
    rilevati in cassa (quadrature) meno l'IVA dei corrispettivi."""
    n = len(mesi)
    zeri = [ZERO] * n
    canali, ricavi_lordi = ricavi_canali(mesi)
    per_voce = costi_per_voce(mesi)
    voci = {v.pk: v for v in VoceConto.objects.all()}
    personale, mesi_personale = costo_personale(mesi)
    quote_amm = ammortamenti(mesi)

    def righe_sezione(sezione):
        elenco = [v for v in voci.values() if v.sezione == sezione and any(per_voce.get(v.pk, zeri))]
        return [_riga('voce', v.nome, per_voce[v.pk], f'voce:{v.pk}', 1) for v in sorted(elenco, key=lambda v: (v.ordine, v.nome))]

    righe = []
    # Ricavi
    righe.append(_riga('titolo', 'Ricavi', zeri))
    giorni_rilevati = None
    dettaglio_rilevato = None
    if base == 'rilevato':
        _, giorni_rilevati = ricavi_rilevati(mesi)
        r = rilevato_per_canale(mesi, ricavi_lordi)
        nomi = dict(CANALI, servito='Servito (lavaggi) - IVA del registratore',
                    prodotti='Vendita prodotti - IVA del registratore',
                    portali='Portali self-service: resto del rilevato - IVA portali')
        voci_ricavo = [_riga('voce', nomi[k], r[k], f'ricavi:{k}', 1) for k, _ in CANALI if any(r[k])]
        dettaglio_rilevato = {k: sum(r[k], ZERO) for k in ('rilevato', 'iva_registratore', 'iva_portali')}
    else:
        voci_ricavo = [_riga('voce', nome, canali[k], f'ricavi:{k}', 1) for k, nome in CANALI if any(canali[k])]
    voci_ricavo += righe_sezione('ricavi')
    righe += voci_ricavo
    ricavi = _somma(zeri, *[r['valori'] for r in voci_ricavo])
    righe.append(_riga('totale', 'Totale ricavi', ricavi, 'tot:ricavi'))

    def sezione(titolo, chiave, extra=()):
        elenco = list(extra) + righe_sezione(chiave)
        totale = _somma(zeri, *[r['valori'] for r in elenco])
        if elenco:
            righe.append(_riga('titolo', titolo, zeri))
            righe.extend(elenco)
            righe.append(_riga('totale', f'Totale {titolo.lower()}', totale, f'tot:{chiave}'))
        return totale

    variabili = sezione('Costi variabili', 'costi_variabili')
    margine = _differenza(ricavi, variabili)
    righe.append(_riga('risultato', 'Margine di contribuzione', margine, 'margine'))
    da_classificare = [_riga('voce', 'Fatture da classificare', per_voce['dacl'], 'dacl', 1)] if any(per_voce.get('dacl', zeri)) else []
    fissi = sezione('Costi fissi', 'costi_fissi', da_classificare)
    dipendenti = [_riga('voce', 'Costo aziendale dei dipendenti', personale, 'personale', 1)] if any(personale) else []
    costo_pers = sezione('Personale', 'personale', dipendenti)
    ebitda = _differenza(_differenza(margine, fissi), costo_pers)
    righe.append(_riga('risultato', 'EBITDA (margine operativo lordo)', ebitda, 'ebitda'))
    cespiti = [_riga('voce', 'Ammortamento cespiti', quote_amm, 'ammortamenti', 1)] if any(quote_amm) else []
    amm = sezione('Ammortamenti', 'ammortamenti', cespiti)
    ebit = _differenza(ebitda, amm)
    righe.append(_riga('risultato', 'EBIT (risultato operativo)', ebit, 'ebit'))
    oneri = sezione('Oneri finanziari', 'oneri_finanziari')
    ante = _differenza(ebit, oneri)
    righe.append(_riga('risultato', 'Risultato prima delle imposte', ante, 'ante_imposte'))
    imposte = sezione('Imposte e tasse', 'imposte')
    righe.append(_riga('risultato', 'Risultato netto', _differenza(ante, imposte), 'netto'))

    totale_ricavi = sum(ricavi, ZERO)
    for r in righe:
        r['pct'] = (r['totale'] / totale_ricavi * 100) if totale_ricavi and r['tipo'] != 'titolo' else None
    return {
        'mesi': mesi,
        'base': base,
        'giorni_rilevati': giorni_rilevati,
        'dettaglio_rilevato': dettaglio_rilevato,
        'righe': righe,
        'ricavi_lordi': ricavi_lordi,
        'mesi_personale': mesi_personale,
        'kpi': {k: sum(v, ZERO) for k, v in (('ricavi', ricavi), ('margine', margine), ('ebitda', ebitda),
                                             ('netto', _differenza(ante, imposte)), ('personale', costo_pers))},
        'serie': {'ricavi': ricavi, 'ebitda': ebitda, 'costi': _differenza(ricavi, ebitda)},
    }


def raggruppa(ce, gruppi):
    """Righe con i valori sommati per gruppi di mesi: [(etichetta, [indici])]."""
    righe = []
    for r in ce['righe']:
        nuova = dict(r)
        if r['chiave'] == 'iniziale':                 # saldi: primo e ultimo mese del gruppo
            nuova['valori'] = [r['valori'][indici[0]] for _, indici in gruppi]
        elif r['chiave'] == 'finale':
            nuova['valori'] = [r['valori'][indici[-1]] for _, indici in gruppi]
        else:
            nuova['valori'] = [sum((r['valori'][i] for i in indici), ZERO) for _, indici in gruppi]
        righe.append(nuova)
    return righe


def pagamenti_senza_fattura(dal=None, al=None):
    """Uscite classificate "Pagamento fornitori" ma non abbinate a una
    fattura: la voce non e' economica, quindi il loro costo manca."""
    qs = MovimentoFinanziario.objects.filter(voce__codice='N.FORNITORI', importo__lt=0, scadenze__isnull=True)
    if dal:
        qs = qs.filter(data__gte=dal, data__lte=al)
    return qs.distinct()


def controlli(mesi, ce=None):
    """Cose da sistemare perche' il conto economico sia completo."""
    from apps.finanze.models import SpesaCassa
    from django.utils import timezone
    dal, al = mesi[0], fine_mese(mesi[-1])
    avvisi = []
    n = FatturaPassiva.objects.filter(classificata=False, competenza_al__gte=dal, competenza_dal__lte=al).count()
    if n:
        avvisi.append(('warning', f'{n} fatture da classificare (il costo e\' in "Fatture da classificare")', 'fatture_dacl'))
    n = MovimentoFinanziario.objects.filter(voce__isnull=True, data__gte=dal, data__lte=al).count()
    if n:
        avvisi.append(('warning', f'{n} movimenti di prima nota da classificare', 'movimenti_dacl'))
    senza_fattura = pagamenti_senza_fattura(dal, al)
    n = senza_fattura.count()
    if n:
        totale = -sum((m.importo for m in senza_fattura), ZERO)
        importo = f'{totale:,.2f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')
        avvisi.append(('warning', f'{n} pagamenti fornitori senza fattura abbinata (€{importo} IVA compresa): '
                                  'il loro costo non e\' nel conto economico. Carica le fatture e abbinale, '
                                  'oppure sposta il movimento su una voce di costo', 'fornitori_senza_fattura'))
    spese =SpesaCassa.objects.filter(data__gte=dal, data__lte=al).count()
    in_prima_nota = MovimentoFinanziario.objects.filter(origine='cassa', impronta__startswith='cassa|spesa|',
                                                        data__gte=dal, data__lte=al).count()
    if spese > in_prima_nota:
        avvisi.append(('warning', f'{spese - in_prima_nota} spese di cassa non ancora in prima nota (Conti > Cassa contanti > Aggiorna)', 'cassa'))
    oggi = timezone.localdate()
    if ce and ce.get('giorni_rilevati') is not None and dal <= oggi:
        trascorsi = (min(al, oggi) - dal).days + 1
        senza = trascorsi - sum(ce['giorni_rilevati'])
        if senza > 0:
            avvisi.append(('warning', f'{senza} giorni senza quadratura: il loro incasso non e\' nel rilevato reale', 'quadrature'))
    presenti = ce['mesi_personale'] if ce else {}
    mancanti = [m for m in mesi if m <= oggi and m not in presenti]
    if mancanti:
        avvisi.append(('info', 'Costo del personale non calcolato per: ' + ', '.join(f'{NOMI_MESI[m.month - 1]} {m.year}' for m in mancanti), 'personale'))
    return avvisi


# ---------------------------------------------------------------- flussi di cassa

FLUSSI = [('operativo', 'Gestione operativa'), ('investimenti', 'Investimenti'), ('finanziario', 'Finanziamenti e titolare')]


def flussi_di_cassa(mesi):
    n = len(mesi)
    zeri = [ZERO] * n
    indice = {m: i for i, m in enumerate(mesi)}
    conti = list(ContoFinanziario.objects.all())
    per_voce = defaultdict(lambda: [ZERO] * n)
    giroconti = [ZERO] * n
    voci = {}
    movimenti = (MovimentoFinanziario.objects.filter(data__gte=mesi[0], data__lte=fine_mese(mesi[-1]))
                 .select_related('voce', 'voce_differenza', 'conto'))
    for m in movimenti:
        if m.data < m.conto.data_saldo_iniziale:
            continue
        i = indice[m.data.replace(day=1)]
        if m.voce and m.voce.flusso == 'giroconto':
            giroconti[i] += m.importo
            continue
        chiave = m.voce_id or 'dacl'
        voci[chiave] = m.voce
        per_voce[chiave][i] += m.importo
        if m.voce_differenza_id and m.differenza:
            # la commissione dentro il pagamento di una fattura esce sulla sua voce
            voci[m.voce_differenza_id] = m.voce_differenza
            per_voce[chiave][i] -= m.differenza
            per_voce[m.voce_differenza_id][i] += m.differenza

    iniziali = [sum((c.saldo_al(m - timedelta(days=1)) for c in conti), ZERO) for m in mesi]
    finali = [sum((c.saldo_al(fine_mese(m)) for c in conti), ZERO) for m in mesi]
    righe = [_riga('risultato', 'Saldo iniziale (tutti i conti)', iniziali, 'iniziale')]
    righe[0]['totale'] = iniziali[0] if iniziali else ZERO
    netti = []
    for flusso, titolo in FLUSSI:
        chiavi = [k for k, v in voci.items() if (v.flusso if v else 'operativo') == flusso]
        # prima le entrate, poi le uscite; da classificare in fondo
        chiavi.sort(key=lambda k: (voci[k] is None, sum(per_voce[k], ZERO) < 0, voci[k].ordine if voci[k] else 0))
        elenco = [_riga('voce', voci[k].nome if voci[k] else 'Movimenti da classificare', per_voce[k],
                        f'voce:{k}' if voci[k] else 'dacl', 1) for k in chiavi]
        totale = _somma(zeri, *[r['valori'] for r in elenco])
        netti.append(totale)
        righe.append(_riga('titolo', titolo, zeri))
        righe.extend(elenco)
        righe.append(_riga('totale', f'Flusso {titolo.lower()}', totale, f'flusso:{flusso}'))
    netto = _somma(zeri, *netti)
    righe.append(_riga('risultato', 'Flusso di cassa netto', netto, 'netto'))
    if any(giroconti):
        righe.append(_riga('voce', 'Giroconti tra conti (si annullano se registrati da entrambe le parti)', giroconti, 'giroconti'))
    finale = _riga('risultato', 'Saldo finale (tutti i conti)', finali, 'finale')
    finale['totale'] = finali[-1] if finali else ZERO
    righe.append(finale)
    for r in righe:
        r['pct'] = None
    per_conto = [{'conto': c, 'iniziale': c.saldo_al(mesi[0] - timedelta(days=1)), 'finale': c.saldo_al(fine_mese(mesi[-1]))}
                 for c in conti]
    return {'mesi': mesi, 'righe': righe, 'per_conto': per_conto,
            'serie': {'netto': netto, 'finale': finali, 'operativo': netti[0]}}
