"""Prima nota: abbinamento dei pagamenti alle scadenze delle fatture,
giroconti tra conti e movimenti della cassa contanti ricavati dalle
quadrature giornaliere."""
import re
import uuid
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from django.db import transaction
from django.db.models import F, Q, Sum

from .models import MovimentoFinanziario, RigaFatturaPassiva, ScadenzaFattura, VoceConto

ZERO = Decimal('0')
GIORNI_ABBINAMENTO = 45
# Con il nome del fornitore nella descrizione il pagamento puo' essere lontano
# dalla scadenza e pagare piu' fatture insieme
GIORNI_COL_NOME = 240
MAX_SCADENZE_GRUPPO = 12
# Bonifico con la commissione dentro: si propone se paga al massimo questo
# in piu' della scadenza e la descrizione ha il nome del fornitore
COMMISSIONE_MAX = Decimal('5.00')
VOCE_COMMISSIONI = 'CF.BANCA'
_PAROLE_VUOTE = {'srl', 'spa', 'snc', 'sas', 'srls', 'societa', 'società', 'italia', 'group', 'servizi', 'della', 'delle',
                 'dei', 'per', 'con', 'sui'}


def _parole(ragione_sociale):
    return {p for p in re.findall(r'[a-zà-ù0-9]{4,}', ragione_sociale.lower()) if p not in _PAROLE_VUOTE}


def nome_nel_testo(ragione_sociale, testo):
    testo = testo.lower()
    if any(p in testo for p in _parole(ragione_sociale)):
        return True
    # nomi corti (ABM, KSM): solo come parola intera
    corti = re.findall(r'\b[a-zà-ù0-9]{3}\b', ragione_sociale.lower())
    return any(re.search(rf'\b{p}\b', testo) for p in corti if p not in _PAROLE_VUOTE)


def scadenze_aperte():
    return (ScadenzaFattura.objects.filter(movimento__isnull=True, pagata_il__isnull=True, importo__gt=0)
            .select_related('fattura__fornitore'))


def proponi_abbinamenti(movimenti=None):
    """Uscite non ancora abbinate con una scadenza aperta entro 45 giorni:
    - dello stesso importo (al centesimo);
    - oppure piu' alte al massimo di COMMISSIONE_MAX, se la descrizione ha
      il nome del fornitore: la differenza e' la commissione del bonifico.
    Vince l'importo esatto, poi il nome del fornitore, poi la piu' vicina.
    Poi le uscite col nome del fornitore (_proposte_col_nome)."""
    tutti = movimenti is None
    if movimenti is None:
        movimenti = (MovimentoFinanziario.objects.filter(importo__lt=0, scadenze__isnull=True)
                     .filter(Q(voce__isnull=True) | Q(voce__codice='N.FORNITORI'))
                     .select_related('conto').order_by('data', 'id'))
    movimenti = list(movimenti)
    aperte = list(scadenze_aperte())
    usate, proposte = set(), {}
    # prima tutti gli importi esatti, poi quelli con la commissione: un
    # bonifico esatto non perde la sua scadenza per uno con commissione
    for con_commissione in (False, True):
        for mov in movimenti:
            if mov.pk in proposte:
                continue
            candidati = []
            for s in aperte:
                commissione = -(mov.importo + s.importo)      # pagato in piu' della scadenza
                if s.pk in usate:
                    continue
                if con_commissione:
                    if not Decimal('0.01') < commissione <= COMMISSIONE_MAX:
                        continue
                elif abs(commissione) > Decimal('0.01'):
                    continue
                giorni = abs((mov.data - (s.data or s.fattura.data)).days)
                if giorni > GIORNI_ABBINAMENTO:
                    continue
                nome = nome_nel_testo(s.fattura.fornitore.ragione_sociale, f'{mov.causale} {mov.descrizione}')
                if nome or not con_commissione:
                    candidati.append((not nome, giorni, s.pk, s, nome, commissione if con_commissione else ZERO))
            if candidati:
                _, giorni, _, s, nome, commissione = min(candidati)
                usate.add(s.pk)
                proposte[mov.pk] = {'movimento': mov, 'scadenza': s, 'scadenze': [s], 'giorni': giorni, 'nome': nome,
                                    'commissione': commissione, 'era_costo': None}
    if tutti:
        _proposte_col_nome(proposte, [s for s in aperte if s.pk not in usate])
    return sorted(proposte.values(), key=lambda p: (p['movimento'].data, p['movimento'].pk))


def _gruppo(scadenze, uscita):
    """Le scadenze che sommate fanno l'uscita, meno al massimo la commissione."""
    prime = scadenze[:MAX_SCADENZE_GRUPPO]
    for n in range(1, len(prime) + 1):
        for gruppo in combinations(prime, n):
            commissione = uscita - sum((s.importo for s in gruppo), ZERO)
            if -Decimal('0.01') <= commissione <= COMMISSIONE_MAX:
                return list(gruppo), max(commissione, ZERO)
    return None


def _proposte_col_nome(proposte, aperte):
    """Uscite col nome del fornitore nella descrizione: possono pagare piu'
    fatture insieme, anche lontane nel tempo. Si guardano anche quelle
    classificate su una voce di costo: con la fattura il costo conterebbe
    due volte, e abbinandole va via. Le commissioni bancarie restano fuori."""
    movimenti = (MovimentoFinanziario.objects.filter(importo__lt=0, scadenze__isnull=True)
                 .filter(Q(voce__isnull=True) | Q(voce__codice__in=('N.FORNITORI', 'N.INVESTIMENTI'))
                         | (~Q(voce__sezione='non_economico') & ~Q(voce__codice=VOCE_COMMISSIONI)))
                 .exclude(pk__in=list(proposte)).exclude(impronta__startswith='conta|')
                 .select_related('conto', 'voce').order_by('importo', 'id'))
    per_fornitore = {}
    for s in aperte:
        per_fornitore.setdefault(s.fattura.fornitore_id, []).append(s)
    for mov in movimenti:
        testo = f'{mov.causale} {mov.descrizione}'
        for scadenze in per_fornitore.values():
            if not scadenze or not nome_nel_testo(scadenze[0].fattura.fornitore.ragione_sociale, testo):
                continue
            vicine = sorted((s for s in scadenze if abs((mov.data - (s.data or s.fattura.data)).days) <= GIORNI_COL_NOME),
                            key=lambda s: abs((mov.data - (s.data or s.fattura.data)).days))
            trovato = _gruppo(vicine, -mov.importo)
            if not trovato:
                continue
            gruppo, commissione = trovato
            for s in gruppo:
                scadenze.remove(s)
            giorni = abs((mov.data - (gruppo[0].data or gruppo[0].fattura.data)).days)
            proposte[mov.pk] = {'movimento': mov, 'scadenza': gruppo[0], 'scadenze': gruppo, 'giorni': giorni, 'nome': True,
                                'commissione': commissione,
                                'era_costo': mov.voce if mov.voce and mov.voce.economica else None}
            break


def abbina(movimento, scadenze, voce_differenza=None, parziale=False):
    """Il movimento paga queste scadenze: diventa "Pagamento fornitori"
    (il costo e' gia' nella fattura). Se l'importo non coincide:
    - parziale (il movimento paga meno delle scadenze): l'ultima scadenza
      si divide e il resto resta aperto, da pagare con un altro mezzo;
      restituisce la scadenza del resto;
    - altrimenti la differenza (es. la commissione del bonifico) va su
      voce_differenza; senza voce l'abbinamento non si fa (ValueError)."""
    differenza = movimento.importo + sum((s.importo for s in scadenze), ZERO)
    if parziale and differenza > Decimal('0.01'):
        ultima = max(scadenze, key=lambda s: (s.data or s.fattura.data, s.pk))
        if differenza >= ultima.importo:
            raise ValueError(f'il resto (€{differenza:.2f}) e\' piu\' della scadenza di {ultima.fattura}: '
                             'togli una delle scadenze scelte')
        with transaction.atomic():
            resto = ScadenzaFattura.objects.create(fattura=ultima.fattura, data=ultima.data, importo=differenza,
                                                   modalita=ultima.modalita, iban=ultima.iban)
            ultima.importo -= differenza
            ultima.save(update_fields=['importo'])
            _abbina(movimento, scadenze, ZERO, None)
        return resto
    if abs(differenza) <= Decimal('0.01'):
        differenza, voce_differenza = ZERO, None
    elif voce_differenza is None:
        raise ValueError(f'il movimento e le scadenze differiscono di €{abs(differenza):.2f}: '
                         'scegli la voce della differenza')
    elif not voce_differenza.economica:
        raise ValueError(f'"{voce_differenza}" non e\' una voce di costo o di ricavo')
    _abbina(movimento, scadenze, differenza, voce_differenza)
    return None


def paga_resto_in_contanti(movimento, scadenze, data=None):
    """Il movimento paga una parte delle scadenze, il resto e' stato pagato
    con la cassa: crea l'uscita su Cassa contanti e la abbina al resto."""
    from .models import ContoFinanziario
    cassa = ContoFinanziario.objects.filter(tipo='cassa', attivo=True).order_by('ordine', 'id').first()
    if cassa is None:
        raise ValueError('non c\'e\' un conto di tipo cassa')
    with transaction.atomic():
        resto = abbina(movimento, scadenze, parziale=True)
        if resto is None:
            raise ValueError('il movimento non paga meno delle scadenze scelte: non c\'e\' un resto')
        contanti = MovimentoFinanziario.objects.create(
            conto=cassa, data=data or movimento.data, importo=-resto.importo, origine='manuale',
            descrizione=f'Pagamento in contanti: {resto.fattura}'[:500], impronta=f'contanti|{uuid.uuid4().hex}')
        abbina(contanti, [resto])
        ricalcola_conte(cassa)
    return contanti


def paga_in_contanti(scadenze, data=None):
    """Scadenze pagate con la cassa: per ognuna l'uscita su Cassa contanti,
    gia' abbinata, nel giorno dato o in quello della scadenza."""
    from .models import ContoFinanziario
    cassa = ContoFinanziario.objects.filter(tipo='cassa', attivo=True).order_by('ordine', 'id').first()
    if cassa is None:
        raise ValueError('non c\'e\' un conto di tipo cassa')
    with transaction.atomic():
        for s in scadenze:
            contanti = MovimentoFinanziario.objects.create(
                conto=cassa, data=data or s.data or s.fattura.data, importo=-s.importo, origine='manuale',
                descrizione=f'Pagamento in contanti: {s.fattura}'[:500], impronta=f'contanti|{uuid.uuid4().hex}')
            abbina(contanti, [s])
        ricalcola_conte(cassa)


def _voce_del_pagamento(movimento, scadenze):
    """Un macchinario pagato resta fra gli investimenti nei flussi di cassa."""
    if movimento.voce and movimento.voce.codice == 'N.INVESTIMENTI':
        return 'N.INVESTIMENTI'
    voci = set(RigaFatturaPassiva.objects.filter(fattura__in={s.fattura_id for s in scadenze})
               .values_list('voce__codice', flat=True))
    return 'N.INVESTIMENTI' if voci == {'N.INVESTIMENTI'} else 'N.FORNITORI'


def _abbina(movimento, scadenze, differenza, voce_differenza):
    with transaction.atomic():
        for s in scadenze:
            s.movimento, s.pagata_il = movimento, movimento.data
            s.save(update_fields=['movimento', 'pagata_il'])
        movimento.voce = VoceConto.objects.get(codice=_voce_del_pagamento(movimento, scadenze))
        movimento.ripartizione, movimento.regola = {}, None
        movimento.differenza, movimento.voce_differenza = differenza, voce_differenza
        movimento.save(update_fields=['voce', 'ripartizione', 'regola', 'differenza', 'voce_differenza'])


def voce_commissioni():
    return VoceConto.objects.filter(codice=VOCE_COMMISSIONI).first()


def togli_abbinamento(scadenza):
    movimento = scadenza.movimento
    with transaction.atomic():
        scadenza.movimento, scadenza.pagata_il = None, None
        scadenza.save(update_fields=['movimento', 'pagata_il'])
        # una scadenza divisa da un pagamento parziale si riunisce alle parti ancora aperte
        aperte = scadenza.fattura.scadenze.filter(movimento__isnull=True, pagata_il__isnull=True, data=scadenza.data,
                                                  modalita=scadenza.modalita).exclude(pk=scadenza.pk)
        for altra in aperte:
            scadenza.importo += altra.importo
            altra.delete()
        scadenza.save(update_fields=['importo'])
        if movimento:
            if movimento.origine == 'manuale' and movimento.impronta.startswith('contanti|') \
                    and not movimento.scadenze.exists():
                conto = movimento.conto
                movimento.delete()                    # uscita di cassa creata col pagamento parziale
                ricalcola_conte(conto)
                return
            # senza una scadenza la differenza calcolata non vale piu'
            movimento.differenza, movimento.voce_differenza = ZERO, None
            if not movimento.scadenze.exists() and movimento.voce and movimento.voce.codice == 'N.FORNITORI':
                movimento.voce = None
            movimento.save(update_fields=['voce', 'differenza', 'voce_differenza'])


def giroconto(movimento, conto):
    """Crea sull'altro conto il movimento opposto (es. versamento dei
    contanti in banca: uscita dalla cassa, entrata in banca)."""
    voce = VoceConto.objects.get(codice='N.GIROCONTO')
    with transaction.atomic():
        opposto = MovimentoFinanziario.objects.create(
            conto=conto, data=movimento.data, importo=-movimento.importo,
            descrizione=f'Giroconto da {movimento.conto}: {movimento.descrizione}'[:500],
            impronta=f'giroconto|{movimento.pk}', voce=voce, origine='manuale', controparte=movimento)
        movimento.voce, movimento.ripartizione, movimento.controparte = voce, {}, opposto
        movimento.save(update_fields=['voce', 'ripartizione', 'controparte'])
    return opposto


# ---------------------------------------------------------------- estratto sul conto sbagliato

def movimenti_di_baps_su_mps():
    """Un estratto BAPS caricato sul conto MPS: gli estratti MPS hanno sempre
    la causale, quelli BAPS mai. Per ogni riga, se il conto BAPS ha gia' lo
    stesso giorno e importo e' un doppione, altrimenti va spostata."""
    from .models import ContoFinanziario
    mps = ContoFinanziario.objects.filter(banca_estratto='MPS').first()
    baps = ContoFinanziario.objects.filter(banca_estratto='BAPS').first()
    if not mps or not baps:
        return None
    fuori = list(mps.movimenti.filter(origine='estratto', causale='').order_by('data', 'id'))
    if not fuori:
        return None
    presenti = {}
    for b in baps.movimenti.filter(origine='estratto', data__gte=fuori[0].data, data__lte=fuori[-1].data):
        presenti.setdefault((b.data, b.importo), []).append(b)
    doppi, da_spostare = [], []
    for m in fuori:
        uguali = presenti.get((m.data, m.importo))
        if uguali:
            doppi.append((m, uguali.pop()))
        else:
            da_spostare.append(m)
    return {'da': mps, 'a': baps, 'doppi': doppi, 'da_spostare': da_spostare, 'n': len(fuori),
            'totale': sum((m.importo for m in fuori), ZERO), 'dal': fuori[0].data, 'al': fuori[-1].data}


def _unisci_doppione(m, b):
    """Prima di togliere il doppione m, su b va quello che ci si era fatto."""
    scadenze = list(m.scadenze.all())
    if scadenze:
        m.scadenze.update(movimento=b)
        b.voce, b.ripartizione, b.regola = m.voce, m.ripartizione, m.regola
        b.differenza, b.voce_differenza = m.differenza, m.voce_differenza
    elif b.voce_id is None and m.voce_id:
        b.voce, b.ripartizione, b.regola = m.voce, m.ripartizione, m.regola
    b.note = b.note or m.note
    opposto = m.controparte
    if opposto and not b.controparte_id and not MovimentoFinanziario.objects.filter(controparte=b).exists():
        MovimentoFinanziario.objects.filter(pk=m.pk).update(controparte=None)
        b.controparte, opposto.controparte = opposto, b
        opposto.save(update_fields=['controparte'])
    b.save()
    m.delete()


def correggi_baps_su_mps():
    r = movimenti_di_baps_su_mps()
    if not r:
        return None
    with transaction.atomic():
        for m, b in r['doppi']:
            _unisci_doppione(m, b)
        for m in r['da_spostare']:
            m.conto = r['a']
            m.save(update_fields=['conto'])
    return len(r['doppi']), len(r['da_spostare'])


# ---------------------------------------------------------------- cassa contanti

VOCI_SPESE_CASSA = {
    'prodotti': 'CV.CONSUMO', 'carburante': 'CF.CARBURANTE', 'manutenzione': 'CF.MANUTENZIONE',
    'personale': 'N.STIPENDI', 'alimentari': 'CF.ALTRI', 'altro': 'CF.ALTRI',
}


# Gli incassi in contanti di ogni quadratura, divisi per fonte: (chiave, voce, descrizione)
FONTI_INCASSI = [
    ('servito', 'N.INC.SERVITO', 'Contanti del servito (registratore meno POS del servito)'),
    ('self', 'N.INC.SELF', 'Contanti delle casse automatiche (vendita totale meno POS)'),
    ('altro', 'N.INC.ALTRO', 'Contanti senza scontrino (operatori, extra, resti e differenze)'),
]


def calendario_quadrature(quadrature):
    """Quale quadratura conta i soldi di un giorno: quella del giorno o, se
    quel giorno non c'e' quadratura, la prima successiva. Restituisce
    (inizio, a_quadratura): inizio e' il giorno dopo la quadratura che
    precede la prima delle date, a_quadratura(giorno) la data della
    quadratura che lo conta (None prima di inizio e dopo l'ultima)."""
    from apps.finanze.models import QuadraturaGiornaliera
    giorni = sorted(q.data for q in quadrature)
    if not giorni:
        return None, lambda giorno: None
    prima = (QuadraturaGiornaliera.objects.filter(data__lt=giorni[0]).order_by('-data')
             .values_list('data', flat=True).first())
    inizio = prima + timedelta(days=1) if prima else giorni[0]

    def a_quadratura(giorno):
        if giorno < inizio:
            return None
        i = bisect_left(giorni, giorno)
        return giorni[i] if i < len(giorni) else None
    return inizio, a_quadratura


def _contanti_da_scontrino(quadrature):
    """{giorno di quadratura: (contanti del servito, contanti delle casse
    automatiche)}. Ogni quadratura conta anche i giorni senza quadratura
    subito prima: quei soldi si contano il giorno dopo.
    - servito: il totale del registratore meno il POS del servito della
      quadratura;
    - casse automatiche (cambia gettoni e portali): la vendita totale delle
      chiusure (contanti e carte, senza ricarica e resto erogato) meno il POS
      delle casse automatiche della quadratura.
    Fino al 27/10/2026 le carte erano scritte nelle banconote e i POS della
    quadratura sono a zero: le toglie l'uscita mensile degli accrediti POS
    (_pos_nei_contanti), uguale nelle due versioni. Spese di cassa e ticket
    di resto non si tolgono: sono uscite a parte."""
    from apps.finanze.models import ChiusuraCassaAutomatica
    if not quadrature:
        return {}
    giorni = sorted(q.data for q in quadrature)
    inizio, a_quadratura = calendario_quadrature(quadrature)
    servito, casse = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for r in (ChiusuraCassaAutomatica.objects.filter(data__gte=inizio, data__lte=giorni[-1])
              .values('data', 'cassa__modalita_registratore')
              .annotate(registratore=Sum('incasso_totale'),
                        vendita=Sum(F('vendita_contante') + F('vendita_non_contante')))):
        g = a_quadratura(r['data'])
        if not g:
            continue
        if r['cassa__modalita_registratore']:
            servito[g] += r['registratore'] or ZERO
        else:
            casse[g] += r['vendita'] or ZERO
    for q in quadrature:
        servito[q.data] -= q.lettore_carte_servito
        casse[q.data] -= q.pos_casse_automatiche
    return {g: (servito[g], casse[g]) for g in giorni}


def incassi_contanti(dal, al):
    """{giorno di quadratura: {'reale', 'servito', 'self'}} dal dal al al:
    i contanti contati (contati - fondo + spese + ticket: i soldi usati per
    spese e ticket sono usciti dagli incassi) e quelli che risultano dagli
    scontrini (vedi _contanti_da_scontrino). Spese e ticket di un giorno
    senza quadratura si riaggiungono alla quadratura che ne conta i soldi."""
    from apps.finanze.models import QuadraturaGiornaliera, SpesaCassa, TicketResto
    quadrature = list(QuadraturaGiornaliera.objects.filter(data__gte=dal, data__lte=al))
    inizio, a_quadratura = calendario_quadrature(quadrature)
    usciti = defaultdict(lambda: ZERO)
    if quadrature:
        ultima = max(q.data for q in quadrature)
        for modello in (SpesaCassa, TicketResto):
            for r in modello.objects.filter(data__gte=inizio, data__lte=ultima).values('data', 'importo'):
                giorno = a_quadratura(r['data'])
                if giorno:
                    usciti[giorno] += r['importo']
    da_scontrino = _contanti_da_scontrino(quadrature)
    return {q.data: {'reale': q.contanti_totali - q.fondo_cassa_iniziale + usciti[q.data],
                     'servito': da_scontrino[q.data][0], 'self': da_scontrino[q.data][1]} for q in quadrature}


def genera_cassa(conto, dal, al):
    """Movimenti della cassa contanti dal dal al al (compresi):
    - incassi in contanti del giorno = contati - fondo + spese + ticket
      (i soldi usati per spese e ticket sono usciti dagli incassi), divisi
      per fonte (FONTI_INCASSI): servito, casse automatiche e, per
      differenza, contanti senza scontrino;
    - ogni spesa pagata con la cassa, sulla voce della sua categoria;
    - ogni ticket di resto ripagato al cliente;
    - ogni versamento di contanti in banca (entrata di un altro conto sulla
      voce Giroconto, con "vers" nella descrizione e senza un giroconto
      fatto a mano): l'uscita dalla cassa dei soldi finiti in banca;
    - se impostato sul conto, il POS contato nei contanti e gli stipendi
      pagati in contanti (vedi _pos_nei_contanti e _stipendi_fuori_busta).
    Si possono rigenerare: gli importi si aggiornano, le voci cambiate a
    mano restano. Restituisce quanti movimenti sono stati scritti."""
    from apps.finanze.models import SpesaCassa, TicketResto

    voci = {v.codice: v for v in VoceConto.objects.filter(
        codice__in=list(VOCI_SPESE_CASSA.values()) + ['N.INCASSI', 'N.GIROCONTO', 'N.TICKET'] + [f[1] for f in FONTI_INCASSI])}
    spese = list(SpesaCassa.objects.filter(data__gte=dal, data__lte=al))
    ticket = list(TicketResto.objects.filter(data__gte=dal, data__lte=al))
    voluti = {}
    for giorno, v in incassi_contanti(dal, al).items():
        parti = {'servito': v['servito'], 'self': v['self'], 'altro': v['reale'] - v['servito'] - v['self']}
        for chiave, codice, descrizione in FONTI_INCASSI:
            if parti[chiave]:
                voluti[f'cassa|incassi|{chiave}|{giorno:%Y-%m-%d}'] = (giorno, parti[chiave], descrizione,
                                                                     voci.get(codice) or voci.get('N.INCASSI'))
    for s in spese:
        voluti[f'cassa|spesa|{s.pk}'] = (s.data, -s.importo, f'Spesa di cassa: {s.descrizione}',
                                         voci.get(VOCI_SPESE_CASSA.get(s.categoria, 'CF.ALTRI')))
    for t in ticket:
        voluti[f'cassa|ticket|{t.pk}'] = (t.data, -t.importo, f'Ticket di resto pagato{": " + t.nota if t.nota else ""}',
                                          voci.get('N.TICKET') or voci.get('N.INCASSI'))
    versamenti = (MovimentoFinanziario.objects.filter(voce__codice='N.GIROCONTO', importo__gt=0, controparte__isnull=True,
                                                      descrizione__icontains='vers', data__gte=dal, data__lte=al)
                  .exclude(conto__tipo='cassa').select_related('conto')
                  .filter(Q(movimentofinanziario__isnull=True)
                          | Q(movimentofinanziario__conto=conto,
                              movimentofinanziario__impronta__startswith='cassa|versamento|')))
    collegati = {}
    for v in versamenti:
        impronta = f'cassa|versamento|{v.pk}'
        voluti[impronta] = (v.data, -v.importo, f'Versamento in banca su {v.conto}', voci.get('N.GIROCONTO'))
        collegati[impronta] = v
    voluti.update(_pos_nei_contanti(conto, dal, al, voci.get('N.INCASSI')))
    if conto.stipendi_in_contanti:
        voluti.update(_stipendi_fuori_busta(dal, al, VoceConto.objects.filter(codice='N.STIPENDI').first()))

    scritti = 0
    with transaction.atomic():
        esistenti = {m.impronta: m for m in conto.movimenti.filter(origine='cassa', data__gte=dal, data__lte=al)}
        for impronta, (giorno, importo, descrizione, voce) in voluti.items():
            m = esistenti.pop(impronta, None)
            if m is None:
                MovimentoFinanziario.objects.create(conto=conto, data=giorno, importo=importo, descrizione=descrizione,
                                                    impronta=impronta, voce=voce, origine='cassa',
                                                    controparte=collegati.get(impronta))
                scritti += 1
            elif (m.data, m.importo, m.descrizione) != (giorno, importo, descrizione):
                m.data, m.importo, m.descrizione = giorno, importo, descrizione
                m.save(update_fields=['data', 'importo', 'descrizione'])
                scritti += 1
        # spese o ticket cancellati nel frattempo; un incasso a zero si toglie
        for m in esistenti.values():
            m.delete()
        conto.movimenti.filter(origine='cassa', importo=0).delete()
        ricalcola_conte(conto)
    return scritti


_POS = re.compile(r'\bp\.?\s?o\.?\s?s\b', re.IGNORECASE)


def _mesi(dal, al):
    mese = dal.replace(day=1)
    while mese <= al:
        fine = (mese + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        yield mese, min(fine, al)
        mese = fine + timedelta(days=1)


_DATA_RIF = re.compile(r'data\s*rif\.?\s*:?\s*(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})', re.IGNORECASE)
GIORNI_ACCREDITO = 10       # le carte arrivano in banca qualche giorno dopo


def giorno_carta(movimento):
    """Il giorno del pagamento con carta dietro un accredito POS: la "Data
    Rif." dell'estratto, altrimenti il giorno dell'accredito."""
    trovato = _DATA_RIF.search(f'{movimento.causale} {movimento.descrizione}')
    if trovato:
        giorno, mese, anno = (int(x) for x in trovato.groups())
        try:
            return date(anno + 2000 if anno < 100 else anno, mese, giorno)
        except ValueError:
            pass
    return movimento.data


def _pos_nei_contanti(conto, dal, al, voce):
    """Fino a conto.pos_nei_contanti_fino_al le quadrature contano le carte
    nei contanti: per ogni mese un'uscita pari agli accrediti POS arrivati
    in banca (entrate sulla voce Incassi con "POS" nella descrizione), che
    toglie dalla cassa i soldi che li' non sono mai entrati. Conta il giorno
    del pagamento con carta (giorno_carta), non quello dell'accredito: le
    carte degli ultimi giorni prima del limite arrivano dopo, quelle di
    prima dell'avvio della cassa non c'erano."""
    limite = conto.pos_nei_contanti_fino_al
    if not limite or dal > limite:
        return {}
    fino = min(al, limite)
    da = max(dal, conto.data_saldo_iniziale)
    accrediti = (MovimentoFinanziario.objects.filter(voce__codice='N.INCASSI', importo__gt=0, data__gte=da,
                                                     data__lte=fino + timedelta(days=GIORNI_ACCREDITO))
                 .exclude(conto__tipo='cassa'))
    per_mese = {}
    for m in accrediti:
        if not _POS.search(f'{m.causale} {m.descrizione}'):
            continue
        giorno = giorno_carta(m)
        if da <= giorno <= fino:
            per_mese[giorno.replace(day=1)] = per_mese.get(giorno.replace(day=1), ZERO) + m.importo
    voluti = {}
    for mese, fine in _mesi(dal, fino):
        if per_mese.get(mese):
            voluti[f'cassa|pos|{mese:%Y-%m}'] = (fine, -per_mese[mese],
                                                 f'Carte di {mese:%m/%Y} contate nei contanti: accreditate in banca col POS',
                                                 voce)
    return voluti


def _stipendi_fuori_busta(dal, al, voce):
    """Parte degli stipendi pagata in contanti, dal Costo personale: per
    ogni dipendente il pagato del mese o, se non e' scritto, il totale da
    pagare (fuori busta + bonus). Un'uscita per mese, all'ultimo giorno."""
    from apps.personale.calcolo import calcola_mese
    from apps.personale.models import MesePersonale

    voluti = {}
    fine_per_mese = dict(_mesi(dal, al))
    for mese_obj in MesePersonale.objects.filter(mese__gte=dal.replace(day=1), mese__lte=al):
        totale = ZERO
        for r in calcola_mese(mese_obj)['righe']:
            totale += max(r['pagato'] if r['pagato'] is not None else r['totale_pagare'], ZERO)
        if totale:
            voluti[f'cassa|stipendi|{mese_obj.mese:%Y-%m}'] = (
                fine_per_mese.get(mese_obj.mese, al), -totale.quantize(Decimal('0.01')),
                f'Stipendi di {mese_obj.mese:%m/%Y} pagati in contanti (fuori busta e bonus)', voce)
    return voluti


def conta_cassa(conto, giorno, contati):
    """Contanti contati a fine giornata: la differenza col saldo calcolato
    diventa un movimento da classificare (uscite in contanti non
    registrate, prelievi, soldi in cassaforte non ancora versati...).
    Una seconda conta nello stesso giorno sostituisce la prima."""
    with transaction.atomic():
        m, _ = MovimentoFinanziario.objects.get_or_create(
            conto=conto, impronta=f'conta|{giorno:%Y-%m-%d}',
            defaults={'data': giorno, 'importo': ZERO, 'origine': 'manuale'})
        m.saldo_estratto = contati
        m.save(update_fields=['saldo_estratto'])
        ricalcola_conte(conto)
    return conto.movimenti.filter(pk=m.pk).first()


def ricalcola_conte(conto):
    """Ogni conta porta il saldo a quello contato: si rifanno in ordine di
    data, perche' i movimenti aggiunti dopo (versamenti, spese) cambiano la
    differenza. Una conta che quadra resta come controllo, a zero."""
    from .indici import euro
    for m in conto.movimenti.filter(impronta__startswith='conta|').order_by('data'):
        importo = m.saldo_estratto - (conto.saldo_al(m.data) - m.importo)
        descrizione = f'Differenza di cassa al {m.data:%d/%m/%Y}: contati {euro(m.saldo_estratto, 2)}'
        if (m.importo, m.descrizione) != (importo, descrizione):
            m.importo, m.descrizione = importo, descrizione
            m.save(update_fields=['importo', 'descrizione'])
