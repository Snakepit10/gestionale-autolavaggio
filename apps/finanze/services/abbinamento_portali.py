"""Abbinamento lavaggi servito <-> transazioni portale da unita' operativa.

Dentro la finestra di una ChiusuraPortali, ogni lavaggio del servito
(ItemOrdine il cui servizio ha `programmi_portale`) viene abbinato alla
transazione WashTec con origine 'unita' di programma compatibile piu'
vicina in orario. Le transazioni 'unita' che restano libere sono i
lavaggi pagati direttamente agli operatori.

Le proposte sono calcolate al volo (nessuna scrittura); diventano
AbbinamentoPortale solo alla conferma dell'operatore.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models.functions import Coalesce

from apps.finanze.models import (PREZZI_PROGRAMMA_PORTALE, AbbinamentoPortale,
                                 TransazionePortale)
from apps.ordini.models import ItemOrdine

TOLLERANZA_DEFAULT = timedelta(hours=2)


def lavaggi_servito(chiusura):
    """Item servito della finestra, con `rif` = orario di riferimento
    (inizio lavorazione, altrimenti creazione ordine)."""
    return list(
        ItemOrdine.objects
        .exclude(servizio_prodotto__programmi_portale='')
        .exclude(ordine__stato='annullato')
        .annotate(rif=Coalesce('inizio_lavorazione', 'ordine__data_ora'))
        .filter(rif__gt=chiusura.periodo_da, rif__lte=chiusura.periodo_a)
        .select_related('ordine__cliente', 'servizio_prodotto')
        .prefetch_related('abbinamenti_portale__transazione',
                          'ordine__items__servizio_prodotto')
        .order_by('rif')
    )


def transazioni_libere(chiusura):
    """Transazioni da unita' operativa della finestra non ancora abbinate."""
    return list(TransazionePortale.objects.filter(
        origine='unita',
        orario__gt=chiusura.periodo_da,
        orario__lte=chiusura.periodo_a,
        abbinamento__isnull=True,
    ).order_by('orario'))


def _slot_liberi(item):
    return max(0, item.quantita - len(item.abbinamenti_portale.all()))


def proponi(chiusura, tolleranza=TOLLERANZA_DEFAULT, items=None, libere=None):
    """Proposte greedy per orario piu' vicino, fino a esaurimento.

    Ogni slot di item (quantita - abbinamenti gia' confermati) e ogni
    transazione libera vengono usati al massimo una volta; si scartano
    le coppie con programma incompatibile o |delta| oltre tolleranza.
    Ritorna [{'item', 'transazione', 'delta_min'}].
    """
    items = lavaggi_servito(chiusura) if items is None else items
    libere = transazioni_libere(chiusura) if libere is None else libere
    tol = tolleranza.total_seconds()

    slots = []
    for it in items:
        slots.extend([it] * _slot_liberi(it))

    coppie = []
    for si, it in enumerate(slots):
        programmi = it.servizio_prodotto.lista_programmi_portale
        for t in libere:
            if t.programma not in programmi:
                continue
            delta = abs((t.orario - it.rif).total_seconds())
            if delta <= tol:
                coppie.append((delta, si, t.pk, t))
    coppie.sort(key=lambda c: (c[0], c[1], c[2]))

    slot_usati, tx_usate, proposte = set(), set(), []
    for delta, si, tpk, t in coppie:
        if si in slot_usati or tpk in tx_usate:
            continue
        slot_usati.add(si)
        tx_usate.add(tpk)
        proposte.append({'item': slots[si], 'transazione': t,
                         'delta_min': round(delta / 60)})
    return proposte


def riepilogo(chiusura, tolleranza=TOLLERANZA_DEFAULT):
    """Dati per la sezione del report giornata.

    - righe: un dict per item servito con abbinamenti confermati,
      proposte, slot ancora scoperti e transazioni libere compatibili
      (per la scelta manuale, ordinate per vicinanza);
    - residuo: transazioni 'unita' che restano libere dopo le proposte
      (= lavaggi pagati agli operatori), con valore da listino.
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
        programmi = it.servizio_prodotto.lista_programmi_portale
        compatibili = []
        if _slot_liberi(it):
            compatibili = sorted(
                (t for t in libere if t.programma in programmi),
                key=lambda t: abs((t.orario - it.rif).total_seconds()))
        righe.append({
            'item': it, 'rif': it.rif,
            'programmi': sorted(programmi),
            'confermati': confermati,
            'proposte': prop,
            'scoperti': scoperti,
            'compatibili': compatibili,
        })

    residuo = [t for t in libere if t.pk not in proposte_tx]
    valore_residuo = sum((PREZZI_PROGRAMMA_PORTALE[t.programma] for t in residuo),
                         Decimal('0.00'))
    senza_prezzo = sum(1 for t in residuo if not PREZZI_PROGRAMMA_PORTALE[t.programma])

    return {
        'righe': righe,
        'n_servito': sum(it.quantita for it in items),
        'n_confermati': n_confermati,
        'n_proposte': len(proposte),
        'n_scoperti': sum(r['scoperti'] for r in righe),
        'residuo': [{'transazione': t,
                     'valore': PREZZI_PROGRAMMA_PORTALE[t.programma]}
                    for t in residuo],
        'valore_residuo': valore_residuo,
        'residuo_senza_prezzo': senza_prezzo,
        'tolleranza_ore': tolleranza.total_seconds() / 3600,
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
