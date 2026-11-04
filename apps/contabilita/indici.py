"""Indici e KPI (Amministrazione > Contabilita' > Indici): redditivita',
struttura dei costi e punto di pareggio, sostenibilita' del debito con
l'indebitamento massimo, liquidita', flussi di cassa, KPI per lavaggio
e valutazione indicativa.

Il periodo sono i mesi chiusi dell'anno da quando parte la contabilita'
(il primo saldo iniziale dei conti), riportati a 12 mesi: banche e
analisti ragionano su valori annui.

Indebitamento massimo, con i due metodi usati dalle banche:
- capacita' di rimborso: flusso al servizio del debito (EBITDA - imposte
  - investimenti di mantenimento) / DSCR minimo = rata annua sostenibile;
  il debito e' il valore attuale di quella rata al tasso e per la durata
  di un nuovo finanziamento;
- multiplo: posizione finanziaria netta (debiti finanziari - liquidita')
  al massimo N volte l'EBITDA.
Il nuovo finanziamento ottenibile e' il minore dei due spazi rimasti
dopo il debito che c'e' gia'.

Manca uno stato patrimoniale completo (crediti, magazzino, patrimonio
netto): ROE, indici di struttura e current ratio pieno non si calcolano;
al loro posto ci sono indici sui cespiti, sulla liquidita' e sui debiti
verso fornitori e banche."""
from datetime import date, timedelta
from decimal import Decimal

from .conto_economico import ZERO, conto_economico, controlli, fine_mese, flussi_di_cassa
from .models import (Cespite, ContoFinanziario, FatturaPassiva, Finanziamento, MovimentoFinanziario,
                     ParametriFinanziari, ScadenzaFattura, aggiungi_mesi)

DURATE = (3, 5, 7, 10)
STRESS_RICAVI = Decimal('10')
MESI_RATE_RECENTI = 3


def rate_recenti(al):
    """Senza finanziamenti registrati: rate pagate (voce Finanziamenti)
    negli ultimi 3 mesi coperti dagli estratti, su base annua. Un debito
    gia' finito, o un acquisto pagato a rate mesi fa, non pesa sul futuro.
    Restituisce (rata annua, movimenti, dal, al)."""
    ultimo = (MovimentoFinanziario.objects.filter(origine='estratto', data__lte=al)
              .order_by('-data').values_list('data', flat=True).first())
    if not ultimo:
        return ZERO, [], None, None
    dal = aggiungi_mesi(ultimo, -MESI_RATE_RECENTI) + timedelta(days=1)
    movimenti = list(MovimentoFinanziario.objects.filter(voce__codice='N.FINANZIAMENTI', importo__lt=0,
                                                         data__gte=dal, data__lte=ultimo)
                     .select_related('conto').order_by('-data'))
    return -sum((m.importo for m in movimenti), ZERO) * 12 / MESI_RATE_RECENTI, movimenti, dal, ultimo


def mesi_del_periodo(anno, oggi):
    """Mesi chiusi dell'anno dall'inizio della contabilita'; se non ce
    n'e' nessuno, anche il mese in corso."""
    inizio = (ContoFinanziario.objects.order_by('data_saldo_iniziale')
              .values_list('data_saldo_iniziale', flat=True).first())
    mesi = [date(anno, m, 1) for m in range(1, 13) if not inizio or date(anno, m, 1) >= inizio.replace(day=1)]
    chiusi = [m for m in mesi if fine_mese(m) < oggi]
    return chiusi or [m for m in mesi if m <= oggi]


def valore_attuale(rata_mensile, tasso, anni):
    """Debito che si rimborsa con quella rata mensile (piano alla francese)."""
    if rata_mensile <= 0:
        return ZERO
    n, i = anni * 12, float(tasso) / 1200
    fattore = n if not i else (1 - (1 + i) ** -n) / i
    return (rata_mensile * Decimal(str(fattore))).quantize(Decimal('1'))


def euro(valore, decimali=0):
    testo = f'{valore:,.{decimali}f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')
    return f'€{testo}'


def _div(a, b):
    return a / b if b else None


def _pct(a, b):
    return a * 100 / b if b else None


def indice(nome, valore, formato, formula, nota='', soglie=None, alto_buono=True, stato=None):
    """formato: euro, euro2, pct, x, giorni, num. soglie = (buono, critico)."""
    if stato is None and valore is not None and soglie:
        buono, critico = soglie
        if alto_buono:
            stato = 'ok' if valore >= buono else 'critico' if valore < critico else 'attenzione'
        else:
            stato = 'ok' if valore <= buono else 'critico' if valore > critico else 'attenzione'
    return {'nome': nome, 'valore': valore, 'formato': formato, 'formula': formula, 'nota': nota, 'stato': stato}


def indici(anno, base='rilevato', oggi=None):
    from django.utils import timezone

    from .analitica import analitica
    oggi = oggi or timezone.localdate()
    mesi = mesi_del_periodo(anno, oggi)
    if not mesi:
        return None
    par = ParametriFinanziari.attuali()
    fattore = Decimal(12) / len(mesi)
    dal, al = mesi[0], fine_mese(mesi[-1])
    giorni = (min(al, oggi) - dal).days + 1

    ce = conto_economico(mesi, base)
    t = {r['chiave']: r['totale'] for r in ce['righe'] if r['chiave']}

    def anno_(chiave):
        return t.get(chiave, ZERO) * fattore

    ricavi, margine, ebitda, ebit = anno_('tot:ricavi'), anno_('margine'), anno_('ebitda'), anno_('ebit')
    fissi, personale, amm = anno_('tot:costi_fissi'), anno_('tot:personale'), anno_('tot:ammortamenti')
    oneri, ante, netto = anno_('tot:oneri_finanziari'), anno_('ante_imposte'), anno_('netto')
    imposte, imposte_stimate = anno_('tot:imposte'), False
    if not imposte:
        imposte, imposte_stimate = max(ante, ZERO) * par.aliquota_imposte / 100, True

    # stesso periodo dell'anno prima, per la crescita
    prima = conto_economico([m.replace(year=m.year - 1) for m in mesi], base)
    ricavi_prima = next((r['totale'] for r in prima['righe'] if r['chiave'] == 'tot:ricavi'), ZERO)

    # debito finanziario e servizio del debito
    finanziamenti = list(Finanziamento.objects.all())
    debito = sum((f.residuo_al(al) for f in finanziamenti), ZERO)
    inizio_12 = al + timedelta(days=1)
    fine_12 = aggiungi_mesi(inizio_12, 12) - timedelta(days=1)
    capitale_12 = interessi_12 = ZERO
    for f in finanziamenti:
        c, i = f.servizio(inizio_12, fine_12)
        capitale_12 += c
        interessi_12 += i
    rate_movimenti = []
    if finanziamenti:
        servizio = capitale_12 + interessi_12
        fonte_servizio = 'rate dei prossimi 12 mesi dal piano dei finanziamenti'
    else:
        rate, rate_movimenti, rate_dal, rate_al = rate_recenti(al)
        servizio = rate + oneri
        fonte_servizio = ('rate pagate (voce Finanziamenti) ' +
                          (f'dal {rate_dal:%d/%m} al {rate_al:%d/%m/%Y}, ' if rate_dal else 'negli ultimi 3 mesi, ') +
                          'su base annua, + interessi del conto economico')
    interessi = oneri or interessi_12
    conti = list(ContoFinanziario.objects.filter(attivo=True))
    saldi_conti = [{'conto': c, 'saldo': c.saldo_al(al), 'conta': c.ultima_conta(al) if c.tipo == 'cassa' else None}
                   for c in conti]
    liquidita = sum((s['saldo'] for s in saldi_conti), ZERO)
    cassa_da_contare = [s['conto'] for s in saldi_conti if s['conto'].tipo == 'cassa' and s['saldo'] and not s['conta']]
    pfn = debito - liquidita

    # capacita' di indebitamento
    capex = amm * par.capex_mantenimento / 100
    cfads = ebitda - imposte - capex
    rata_max = max(cfads, ZERO) / par.dscr_minimo
    rata_libera = max(rata_max - servizio, ZERO)
    debito_max_dscr = valore_attuale(rata_max / 12, par.tasso_nuovo_debito, par.durata_nuovo_debito)
    pfn_max = max(ebitda, ZERO) * par.multiplo_ebitda
    spazio_multiplo = max(pfn_max - pfn, ZERO)
    nuovo_dscr = valore_attuale(rata_libera / 12, par.tasso_nuovo_debito, par.durata_nuovo_debito)
    nuovo = min(nuovo_dscr, spazio_multiplo).quantize(Decimal('1'))
    # con spazio: il debito di oggi piu' il nuovo; senza: il massimo sostenibile, sotto il debito di oggi
    massimo = debito + nuovo if nuovo > 0 else min(debito_max_dscr, pfn_max + liquidita)
    capacita = {
        'cfads': cfads, 'capex': capex, 'imposte': imposte, 'imposte_stimate': imposte_stimate,
        'rata_max': rata_max, 'rata_max_mese': rata_max / 12, 'servizio': servizio, 'fonte_servizio': fonte_servizio,
        'rata_libera': rata_libera, 'rata_libera_mese': rata_libera / 12,
        'debito_max_dscr': debito_max_dscr, 'pfn_max': pfn_max, 'spazio_multiplo': spazio_multiplo,
        'nuovo_dscr': nuovo_dscr, 'nuovo': nuovo,
        'vincolo': 'multiplo' if spazio_multiplo < nuovo_dscr else 'dscr',
        'massimo': massimo.quantize(Decimal('1')), 'oltre': debito > massimo,
        'debito': debito, 'liquidita': liquidita, 'pfn': pfn,
        'saldi_conti': saldi_conti, 'cassa_da_contare': cassa_da_contare, 'rate_movimenti': rate_movimenti,
    }
    capacita['durate'] = []
    for n in DURATE:
        importo = min(valore_attuale(rata_libera / 12, par.tasso_nuovo_debito, n), spazio_multiplo)
        capacita['durate'].append({'anni': n, 'nuovo': importo, 'rata': _rata(importo, par.tasso_nuovo_debito, n)})

    # punto di pareggio
    mdc = _div(margine, ricavi)
    bep_operativo = (fissi + personale) / mdc if mdc and mdc > 0 else None
    bep_economico = (fissi + personale + amm + oneri) / mdc if mdc and mdc > 0 else None
    bep_cassa = (fissi + personale + servizio + imposte) / mdc if mdc and mdc > 0 else None
    dscr = _div(cfads, servizio)
    dscr_stress = _div(cfads - margine * STRESS_RICAVI / 100, servizio)

    # fornitori e flussi
    aperte = list(ScadenzaFattura.objects.filter(pagata_il__isnull=True, importo__gt=0).values_list('data', 'importo'))
    debiti_fornitori = sum((i for _, i in aperte), ZERO)
    scaduti = sum((i for d, i in aperte if d and d < oggi), ZERO)
    acquisti = sum(FatturaPassiva.objects.filter(data__gte=dal, data__lte=al).values_list('totale', flat=True), ZERO) * fattore
    fc = flussi_di_cassa(mesi)
    flussi = {r['chiave']: r['totale'] for r in fc['righe'] if r['chiave'].startswith('flusso:')}
    operativo = flussi.get('flusso:operativo', ZERO) * fattore
    investimenti = flussi.get('flusso:investimenti', ZERO) * fattore
    costi_monetari = ricavi - ebitda
    # senza estratti conto caricati i flussi sono vuoti, non zero
    con_movimenti = MovimentoFinanziario.objects.filter(data__gte=dal, data__lte=al).exists()
    senza_estratti = '' if con_movimenti else 'carica gli estratti conto del periodo'

    # cespiti e volumi
    cespiti = [c for c in Cespite.objects.all() if c.data_acquisto <= al]
    lordo = sum((c.valore for c in cespiti), ZERO)
    netto_cespiti = lordo - sum((c.ammortizzato_al(al.replace(day=1)) for c in cespiti), ZERO)
    a = analitica(mesi, base)
    sv, sf = a['servito'], a['self']

    sezioni = [
        {'titolo': 'Redditività', 'icona': 'graph-up', 'indici': [
            indice('Crescita dei ricavi', _pct(t.get('tot:ricavi', ZERO) - ricavi_prima, ricavi_prima), 'pct',
                   'ricavi del periodo / stesso periodo anno prima - 1', soglie=(5, 0)),
            indice('Margine di contribuzione', _pct(margine, ricavi), 'pct', 'ricavi - costi variabili, su ricavi',
                   'quanto resta di ogni euro venduto per coprire i costi fissi', soglie=(65, 50)),
            indice('EBITDA margin', _pct(ebitda, ricavi), 'pct', 'EBITDA / ricavi',
                   'la misura di redditività usata dalle banche', soglie=(20, 10)),
            indice('ROS (EBIT margin)', _pct(ebit, ricavi), 'pct', 'EBIT / ricavi', 'dopo gli ammortamenti', soglie=(10, 3)),
            indice('Utile netto sui ricavi', _pct(netto, ricavi), 'pct', 'risultato netto / ricavi', soglie=(8, 0)),
            indice('ROI sugli impianti', _pct(ebit, netto_cespiti), 'pct', 'EBIT annuo / valore netto dei cespiti',
                   'rendimento del capitale investito in impianti', soglie=(15, 5)),
            indice('Rotazione degli impianti', _div(ricavi, lordo), 'x', 'ricavi annui / valore dei cespiti',
                   'euro di ricavo per euro investito'),
        ]},
        {'titolo': 'Struttura dei costi e pareggio', 'icona': 'bullseye', 'indici': [
            indice('Incidenza del personale', _pct(personale, ricavi), 'pct', 'costo del personale / ricavi',
                   soglie=(35, 50), alto_buono=False),
            indice('Incidenza dei costi fissi', _pct(fissi + personale + amm, ricavi), 'pct',
                   '(costi fissi + personale + ammortamenti) / ricavi', 'rigidità della struttura', soglie=(60, 80), alto_buono=False),
            indice('Pareggio operativo', bep_operativo, 'euro', '(costi fissi + personale) / margine di contribuzione %',
                   'ricavi annui per EBITDA = 0' + (f' ({euro(bep_operativo / 12)} al mese)' if bep_operativo else '')),
            indice('Pareggio economico', bep_economico, 'euro', '(costi fissi + personale + ammortamenti + oneri) / MdC %',
                   'ricavi annui per utile ante imposte = 0' + (f' ({euro(bep_economico / 12)} al mese)' if bep_economico else '')),
            indice('Pareggio di cassa', bep_cassa, 'euro', '(costi fissi + personale + rate + imposte) / MdC %',
                   'ricavi annui per pagare tutto, rate comprese' + (f' ({euro(bep_cassa / 12)} al mese)' if bep_cassa else '')),
            indice('Margine di sicurezza', _pct(ricavi - bep_economico, ricavi) if bep_economico else None, 'pct',
                   '(ricavi - pareggio economico) / ricavi', 'di quanto possono calare i ricavi prima della perdita', soglie=(20, 10)),
            indice('Leva operativa', _div(margine, ebit) if ebit > 0 else None, 'x', 'margine di contribuzione / EBIT',
                   'se i ricavi calano dell\'1%, l\'EBIT cala di tante volte l\'1%', soglie=(3, 6), alto_buono=False),
        ]},
        {'titolo': 'Debito e sostenibilità', 'icona': 'bank', 'indici': [
            indice('Posizione finanziaria netta', pfn, 'euro', 'debiti finanziari - liquidità',
                   'negativa = più cassa che debiti', stato='ok' if pfn <= 0 else None),
            indice('PFN / EBITDA', _div(pfn, ebitda) if ebitda > 0 else None, 'x', 'anni di EBITDA per ripagare il debito netto',
                   f'le banche accettano di solito fino a {par.multiplo_ebitda:.1f}x'.replace('.', ','),
                   soglie=(2, par.multiplo_ebitda), alto_buono=False,
                   stato='ok' if pfn <= 0 else ('critico' if ebitda <= 0 and pfn > 0 else None)),
            indice('DSCR', dscr, 'x', '(EBITDA - imposte - investimenti di mantenimento) / rate annue',
                   f'sotto 1 il flusso non paga le rate (segnale d\'allerta del Codice della crisi); con ricavi -{STRESS_RICAVI:.0f}%: '
                   + (f'{dscr_stress:.2f}x'.replace('.', ',') if dscr_stress is not None else 'n.d.'),
                   soglie=(par.dscr_minimo, 1)),
            indice('Copertura degli interessi', _div(ebitda, interessi), 'x', 'EBITDA / interessi passivi', soglie=(5, 3)),
            indice('Oneri finanziari sui ricavi', _pct(interessi, ricavi), 'pct', 'interessi / ricavi', soglie=(2, 4), alto_buono=False),
            indice('Debito finanziario', debito, 'euro', 'residuo dei finanziamenti registrati',
                   '' if finanziamenti else 'nessun finanziamento registrato'),
        ]},
        {'titolo': 'Liquidità e cassa', 'icona': 'cash-stack', 'indici': [
            indice('Liquidità', liquidita, 'euro', f'saldo dei conti al {al:%d/%m/%Y}'),
            indice('Giorni di cassa', _div(liquidita * 365, costi_monetari) if liquidita > 0 else ZERO, 'giorni',
                   'liquidità / costi operativi giornalieri', 'quanti giorni si va avanti senza incassi', soglie=(60, 30)),
            indice('Liquidità sugli impegni a breve', _div(liquidita, debiti_fornitori + servizio), 'x',
                   'liquidità / (fornitori da pagare + rate 12 mesi)', 'quick ratio semplificato', soglie=(1.2, 1)),
            indice('Conversione in cassa', _pct(operativo, ebitda) if con_movimenti and ebitda > 0 else None,
                   'pct', 'flusso operativo / EBITDA', senza_estratti or 'quanto EBITDA diventa davvero cassa', soglie=(80, 50)),
            indice('Free cash flow', operativo + investimenti if con_movimenti else None, 'euro',
                   'flusso operativo + investimenti, su base annua',
                   senza_estratti or 'cassa libera per rate, titolare e riserve', soglie=(0, 0)),
            indice('Giorni di pagamento fornitori (DPO)', _div(debiti_fornitori * 365, acquisti), 'giorni',
                   'debiti verso fornitori / acquisti annui IVA compresa'),
            indice('Fornitori scaduti', scaduti, 'euro', 'scadenze passate non pagate',
                   stato='attenzione' if scaduti > 0 else 'ok'),
        ]},
        {'titolo': 'KPI operativi', 'icona': 'speedometer2', 'indici': [
            indice('Ricavo medio al giorno', _div(t.get('tot:ricavi', ZERO), giorni), 'euro', 'ricavi / giorni del periodo'),
            indice('Lavaggi del servito al giorno', _div(Decimal(sv['lavaggi']), giorni) if sv['lavaggi'] else None, 'num',
                   'ordini con lavaggi / giorni'),
            indice('Ricavo per lavaggio servito', sv['ricavo_lavaggio'], 'euro2', 'ricavi del servito / lavaggi',
                   f"costo pieno {euro(sv['costo_lavaggio'], 2)}" if sv['costo_lavaggio'] else ''),
            indice('Ricavo per ora lavorata', sv['ricavo_ora'], 'euro2', 'ricavi del servito / ore del personale servito',
                   f"costo pieno {euro(sv['costo_ora'], 2)} l'ora" if sv['costo_ora'] else ''),
            indice('Cicli self al giorno', _div(Decimal(sf['cicli']), giorni) if sf['cicli'] else None, 'num', 'cicli self dei portali / giorni'),
            indice('Ricavo per ciclo self', sf['ricavo_ciclo'], 'euro2', 'ricavi dei portali / cicli self'),
            indice('Produttività del personale', _div(ricavi, personale), 'x', 'ricavi / costo del personale',
                   'euro di ricavo per euro di stipendio', soglie=(3, 2)),
        ]},
    ]
    valutazione = None
    if ebitda > 0:
        ev = ebitda * par.multiplo_valutazione
        valutazione = {'ev': ev, 'equity': ev - pfn}
    return {
        'mesi': mesi, 'dal': dal, 'al': al, 'fattore': fattore, 'parametri': par,
        'conto': {'ricavi': ricavi, 'margine': margine, 'ebitda': ebitda, 'ebit': ebit, 'netto': netto,
                  'ammortamenti': amm, 'oneri': oneri},
        'capacita': capacita, 'dscr': dscr, 'pfn_ebitda': _div(pfn, ebitda) if ebitda > 0 else None,
        'sezioni': sezioni, 'valutazione': valutazione, 'finanziamenti': len(finanziamenti),
        'controlli': controlli(mesi, ce),
    }


def _rata(debito, tasso, anni):
    n, i = anni * 12, float(tasso) / 1200
    if debito <= 0:
        return ZERO
    if not i:
        return debito / n
    return (debito * Decimal(str(i / (1 - (1 + i) ** -n)))).quantize(Decimal('1'))
