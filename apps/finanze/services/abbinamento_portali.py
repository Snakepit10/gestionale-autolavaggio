"""Abbinamento lavaggi servito <-> transazioni portale da unita' operativa.

Dentro la finestra di una ChiusuraPortali, ogni lavaggio del servito
(ItemOrdine il cui servizio ha `programmi_portale`) viene abbinato a una
transazione WashTec con origine 'unita' compatibile. Le transazioni
'unita' che restano libere sono i lavaggi pagati direttamente agli
operatori.

Regole di proposta:
- PRIORITA' DI PROGRAMMA: l'abbinamento procede a giri, uno per
  posizione nell'elenco `programmi_portale` del servizio (es. '8,9,7,4':
  prima tutti i P8, poi P9, P7 e infine P4). In ogni giro si assegna per
  vicinanza d'orario, fino a esaurimento.
- ORARIO: il passaggio al portale avviene prima della fine del lavoro,
  quindi si parte dal COMPLETAMENTO (fine_lavorazione) e si cerca
  all'indietro a partire da ANTICIPO_PORTALE (~30 min): obiettivo =
  completamento - 30 min, transazioni non successive al completamento
  e non oltre TOLLERANZA prima dell'obiettivo. Se l'item non ha
  completamento registrato, o l'ordine e' stato chiuso in ritardo (oltre
  DURATA_MAX_LAVORO dalla creazione), si ripiega sull'ora di creazione
  dell'ordine (ricerca simmetrica, +-TOLLERANZA).
- RIPIEGO: dopo i giri, i lavaggi ancora scoperti che ammettono il P4
  (quindi non i completi a mano, solo P5) prendono i P4 liberi della
  finestra senza vincoli d'orario, dal piu' vicino.

Le proposte sono calcolate al volo (nessuna scrittura); diventano
AbbinamentoPortale solo alla conferma dell'operatore.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from apps.finanze.models import (PREZZI_PROGRAMMA_PORTALE, AbbinamentoPortale,
                                 TransazionePortale)
from apps.ordini.models import ItemOrdine

ANTICIPO_PORTALE = timedelta(minutes=30)
TOLLERANZA_DEFAULT = timedelta(hours=2)
# Oltre questa durata creazione -> completamento l'ordine e' stato
# chiuso in ritardo (es. la mattina dopo): il completamento non dice
# quando l'auto e' passata dal portale, quindi si usa la creazione.
DURATA_MAX_LAVORO = timedelta(hours=3)
# Programma con cui si coprono, senza vincoli d'orario, i lavaggi servito
# rimasti scoperti dopo i giri di priorita'.
PROGRAMMA_RIPIEGO = 4
# Margine per caricare gli ordini a cavallo dell'inizio finestra
_MARGINE_CARICAMENTO = timedelta(hours=12)


def _riferimento(item):
    """(obiettivo, limite_max, tipo, orario_mostrato) dell'item.

    tipo 'completato': obiettivo = fine - ANTICIPO, limite = fine
    (la transazione non puo' essere successiva al completamento);
    tipo 'creato' (nessun completamento) o 'tardivo' (completamento
    oltre DURATA_MAX_LAVORO dalla creazione): obiettivo = creazione
    ordine, nessun limite.
    """
    creato = item.ordine.data_ora
    fine = item.fine_lavorazione
    if fine and fine - creato <= DURATA_MAX_LAVORO:
        return fine - ANTICIPO_PORTALE, fine, 'completato', fine
    return creato, None, ('tardivo' if fine else 'creato'), creato


def _compatibile_orario(item, t, tolleranza):
    """Distanza (secondi) dall'obiettivo, o None se fuori dai limiti."""
    obiettivo, limite, _, _ = item.riferimento
    if limite is not None and t.orario > limite:
        return None
    delta = abs((t.orario - obiettivo).total_seconds())
    return delta if delta <= tolleranza.total_seconds() else None


def lavaggi_servito(chiusura):
    """Item servito il cui obiettivo cade nella finestra di chiusura.
    Ogni item riceve l'attributo `riferimento` (vedi _riferimento)."""
    candidati = (
        ItemOrdine.objects
        .exclude(servizio_prodotto__programmi_portale='')
        .exclude(ordine__stato='annullato')
        .filter(ordine__data_ora__gt=chiusura.periodo_da - _MARGINE_CARICAMENTO,
                ordine__data_ora__lte=chiusura.periodo_a)
        .select_related('ordine__cliente', 'servizio_prodotto')
        .prefetch_related('abbinamenti_portale__transazione',
                          'ordine__items__servizio_prodotto')
    )
    items = []
    for it in candidati:
        it.riferimento = _riferimento(it)
        if chiusura.periodo_da < it.riferimento[0] <= chiusura.periodo_a:
            it.rif = it.riferimento[3]
            items.append(it)
    items.sort(key=lambda i: i.riferimento[0])
    return items


def transazioni_libere(chiusura):
    """Transazioni da unita' operativa della finestra (di ciascun
    portale) non ancora abbinate."""
    return list(TransazionePortale.objects.filter(
        chiusura.q_transazioni(),
        origine='unita',
        abbinamento__isnull=True,
    ).order_by('orario'))


def _slot_liberi(item):
    return max(0, item.quantita - len(item.abbinamenti_portale.all()))


def proponi(chiusura, tolleranza=TOLLERANZA_DEFAULT, items=None, libere=None):
    """Proposte a giri di priorita' di programma, per vicinanza d'orario.

    Giro k: solo coppie in cui il programma della transazione e' il
    k-esimo nell'elenco di priorita' del servizio. Ogni slot di item
    (quantita - abbinamenti confermati) e ogni transazione si usano al
    massimo una volta. Ritorna [{'item', 'transazione', 'delta_min'}]
    con delta_min = minuti tra transazione e completamento (o creazione).
    """
    items = lavaggi_servito(chiusura) if items is None else items
    libere = transazioni_libere(chiusura) if libere is None else libere

    slots = []
    for it in items:
        slots.extend([it] * _slot_liberi(it))
    n_giri = max((len(it.servizio_prodotto.programmi_portale_ordinati)
                  for it in items), default=0)

    slot_usati, tx_usate, proposte = set(), set(), []
    for giro in range(n_giri):
        coppie = []
        for si, it in enumerate(slots):
            if si in slot_usati:
                continue
            priorita = it.servizio_prodotto.programmi_portale_ordinati
            if giro >= len(priorita):
                continue
            programma = priorita[giro]
            for t in libere:
                if t.pk in tx_usate or t.programma != programma:
                    continue
                delta = _compatibile_orario(it, t, tolleranza)
                if delta is not None:
                    coppie.append((delta, si, t.pk, t))
        coppie.sort(key=lambda c: (c[0], c[1], c[2]))
        for delta, si, tpk, t in coppie:
            if si in slot_usati or tpk in tx_usate:
                continue
            slot_usati.add(si)
            tx_usate.add(tpk)
            it = slots[si]
            proposte.append({
                'item': it, 'transazione': t, 'giro': giro + 1,
                'delta_min': round((it.riferimento[3] - t.orario).total_seconds() / 60),
                'fuori_orario': False,
            })

    # Ultimo giro: i lavaggi rimasti scoperti che ammettono il P4 (non i
    # completi a mano) prendono i P4 liberi della finestra anche senza
    # coincidenza d'orario, sempre partendo dal piu' vicino.
    coppie = []
    for si, it in enumerate(slots):
        if si in slot_usati or PROGRAMMA_RIPIEGO not in it.servizio_prodotto.lista_programmi_portale:
            continue
        for t in libere:
            if t.pk not in tx_usate and t.programma == PROGRAMMA_RIPIEGO:
                coppie.append((abs((t.orario - it.riferimento[0]).total_seconds()), si, t.pk, t))
    coppie.sort(key=lambda c: (c[0], c[1], c[2]))
    for delta, si, tpk, t in coppie:
        if si in slot_usati or tpk in tx_usate:
            continue
        slot_usati.add(si)
        tx_usate.add(tpk)
        it = slots[si]
        proposte.append({
            'item': it, 'transazione': t, 'giro': n_giri + 1,
            'delta_min': round((it.riferimento[3] - t.orario).total_seconds() / 60),
            'fuori_orario': True,
        })
    return proposte


def riepilogo(chiusura, tolleranza=TOLLERANZA_DEFAULT):
    """Dati per la sezione del report giornata.

    - righe: un dict per item servito con abbinamenti confermati,
      proposte, slot scoperti e transazioni libere compatibili (per la
      scelta manuale: prima per priorita' di programma, poi vicinanza);
    - residuo: transazioni 'unita' che restano libere dopo le proposte
      (= lavaggi pagati agli operatori), con valore da listino e valore
      incassato (diverso dal listino se rettificato: omaggio, promo...).
    """
    items = lavaggi_servito(chiusura)
    libere = transazioni_libere(chiusura)
    proposte = proponi(chiusura, tolleranza, items=items, libere=libere)

    proposte_per_item = {}
    for p in proposte:
        proposte_per_item.setdefault(p['item'].pk, []).append(p)
    proposte_tx = {p['transazione'].pk for p in proposte}

    righe = []
    n_confermati = 0
    for it in items:
        confermati = list(it.abbinamenti_portale.all())
        n_confermati += len(confermati)
        prop = proposte_per_item.get(it.pk, [])
        scoperti = max(0, it.quantita - len(confermati) - len(prop))
        priorita = it.servizio_prodotto.programmi_portale_ordinati
        obiettivo = it.riferimento[0]
        compatibili = []
        if _slot_liberi(it):
            compatibili = sorted(
                (t for t in libere if t.programma in priorita),
                key=lambda t: (priorita.index(t.programma),
                               abs((t.orario - obiettivo).total_seconds())))
        righe.append({
            'item': it, 'rif': it.riferimento[3],
            'rif_tipo': it.riferimento[2],
            'programmi': priorita,
            'confermati': confermati,
            'proposte': prop,
            'scoperti': scoperti,
            'compatibili': compatibili,
        })

    from apps.finanze.models import RettificaResiduo

    residuo = [t for t in libere if t.pk not in proposte_tx]
    rettifiche = {r.transazione_id: r for r in RettificaResiduo.objects.filter(
        transazione__in=residuo).select_related('operatore')}
    righe_residuo = []
    for t in residuo:
        listino = PREZZI_PROGRAMMA_PORTALE[t.programma]
        rettifica = rettifiche.get(t.pk)
        righe_residuo.append({'transazione': t, 'listino': listino, 'rettifica': rettifica,
                              'valore': rettifica.importo if rettifica else listino})
    valore_listino = sum((r['listino'] for r in righe_residuo), Decimal('0.00'))
    valore_residuo = sum((r['valore'] for r in righe_residuo), Decimal('0.00'))
    senza_prezzo = sum(1 for t in residuo if not PREZZI_PROGRAMMA_PORTALE[t.programma])

    return {
        'righe': righe,
        'n_servito': sum(it.quantita for it in items),
        'n_confermati': n_confermati,
        'n_proposte': len(proposte),
        'n_scoperti': sum(r['scoperti'] for r in righe),
        'n_senza_completamento': sum(1 for r in righe if r['rif_tipo'] != 'completato'),
        'residuo': righe_residuo,
        # incassato dagli operatori (usato in quadratura) e a listino
        'valore_residuo': valore_residuo,
        'valore_residuo_listino': valore_listino,
        'rettifica_residuo': valore_listino - valore_residuo,
        'n_rettificati': len(rettifiche),
        'residuo_senza_prezzo': senza_prezzo,
        'tolleranza_ore': tolleranza.total_seconds() / 3600,
        'anticipo_min': int(ANTICIPO_PORTALE.total_seconds() / 60),
    }


def conferma_proposte(chiusura, operatore=None, tolleranza=TOLLERANZA_DEFAULT):
    """Salva come abbinamenti le proposte correnti (ricalcolate qui).
    Idempotente: una seconda chiamata non trova piu' nulla da proporre."""
    with transaction.atomic():
        proposte = proponi(chiusura, tolleranza)
        AbbinamentoPortale.objects.bulk_create([
            AbbinamentoPortale(item=p['item'], transazione=p['transazione'],
                               operatore=operatore)
            for p in proposte
        ], ignore_conflicts=True)
    return len(proposte)


def abbina_manuale(chiusura, item_id, transazione_id, operatore=None):
    """Abbinamento scelto a mano. Ritorna (ok, messaggio)."""
    item = next((i for i in lavaggi_servito(chiusura) if i.pk == item_id), None)
    if item is None:
        return False, 'Lavaggio servito non presente in questa chiusura.'
    if not _slot_liberi(item):
        return False, 'Il lavaggio ha gia\' tutti i suoi abbinamenti.'
    t = next((t for t in transazioni_libere(chiusura) if t.pk == transazione_id), None)
    if t is None:
        return False, 'Transazione non disponibile (gia\' abbinata o fuori finestra).'
    if t.programma not in item.servizio_prodotto.lista_programmi_portale:
        return False, f'Programma P{t.programma} non compatibile con il servizio.'
    AbbinamentoPortale.objects.create(item=item, transazione=t, operatore=operatore)
    return True, 'Abbinamento salvato.'
