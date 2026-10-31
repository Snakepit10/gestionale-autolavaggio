"""Prima nota: abbinamento dei pagamenti alle scadenze delle fatture,
giroconti tra conti e movimenti della cassa contanti ricavati dalle
quadrature giornaliere."""
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .models import MovimentoFinanziario, ScadenzaFattura, VoceConto

GIORNI_ABBINAMENTO = 45
_PAROLE_VUOTE = {'srl', 'spa', 'snc', 'sas', 'srls', 'societa', 'società', 'italia', 'group', 'servizi', 'della', 'delle'}


def _parole(ragione_sociale):
    return {p for p in re.findall(r'[a-zà-ù0-9]{4,}', ragione_sociale.lower()) if p not in _PAROLE_VUOTE}


def nome_nel_testo(ragione_sociale, testo):
    testo = testo.lower()
    return any(p in testo for p in _parole(ragione_sociale))


def scadenze_aperte():
    return (ScadenzaFattura.objects.filter(movimento__isnull=True, pagata_il__isnull=True, importo__gt=0)
            .select_related('fattura__fornitore'))


def proponi_abbinamenti(movimenti=None):
    """Uscite non ancora abbinate con una scadenza aperta dello stesso
    importo (al centesimo) entro 45 giorni; a parita' vince quella col
    nome del fornitore nella descrizione, poi la piu' vicina."""
    if movimenti is None:
        movimenti = (MovimentoFinanziario.objects.filter(importo__lt=0, scadenze__isnull=True)
                     .filter(Q(voce__isnull=True) | Q(voce__codice='N.FORNITORI'))
                     .select_related('conto').order_by('data', 'id'))
    aperte = list(scadenze_aperte())
    usate, proposte = set(), []
    for mov in movimenti:
        candidati = []
        for s in aperte:
            if s.pk in usate or abs(s.importo + mov.importo) > Decimal('0.01'):
                continue
            giorni = abs((mov.data - (s.data or s.fattura.data)).days)
            if giorni <= GIORNI_ABBINAMENTO:
                nome = nome_nel_testo(s.fattura.fornitore.ragione_sociale, f'{mov.causale} {mov.descrizione}')
                candidati.append((not nome, giorni, s.pk, s, nome))
        if candidati:
            _, giorni, _, s, nome = min(candidati)
            usate.add(s.pk)
            proposte.append({'movimento': mov, 'scadenza': s, 'giorni': giorni, 'nome': nome})
    return proposte


def abbina(movimento, scadenze):
    """Il movimento paga queste scadenze: diventa "Pagamento fornitori"
    (il costo e' gia' nella fattura)."""
    with transaction.atomic():
        for s in scadenze:
            s.movimento, s.pagata_il = movimento, movimento.data
            s.save(update_fields=['movimento', 'pagata_il'])
        movimento.voce = VoceConto.objects.get(codice='N.FORNITORI')
        movimento.ripartizione, movimento.regola = {}, None
        movimento.save(update_fields=['voce', 'ripartizione', 'regola'])


def togli_abbinamento(scadenza):
    movimento = scadenza.movimento
    with transaction.atomic():
        scadenza.movimento, scadenza.pagata_il = None, None
        scadenza.save(update_fields=['movimento', 'pagata_il'])
        if movimento and not movimento.scadenze.exists() and movimento.voce and movimento.voce.codice == 'N.FORNITORI':
            movimento.voce = None
            movimento.save(update_fields=['voce'])


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


# ---------------------------------------------------------------- cassa contanti

VOCI_SPESE_CASSA = {
    'prodotti': 'CV.CONSUMO', 'carburante': 'CF.CARBURANTE', 'manutenzione': 'CF.MANUTENZIONE',
    'personale': 'N.STIPENDI', 'alimentari': 'CF.ALTRI', 'altro': 'CF.ALTRI',
}


def genera_cassa(conto, dal, al):
    """Movimenti della cassa contanti dal dal al al (compresi):
    - incassi in contanti del giorno = contati - fondo + spese + ticket
      (i soldi usati per spese e ticket sono usciti dagli incassi);
    - ogni spesa pagata con la cassa, sulla voce della sua categoria;
    - ogni ticket di resto ripagato al cliente.
    Si possono rigenerare: gli importi si aggiornano, le voci cambiate a
    mano restano. Restituisce quanti movimenti sono stati scritti."""
    from apps.finanze.models import QuadraturaGiornaliera, SpesaCassa, TicketResto

    voci = {v.codice: v for v in VoceConto.objects.filter(codice__in=list(VOCI_SPESE_CASSA.values()) + ['N.INCASSI'])}
    spese = list(SpesaCassa.objects.filter(data__gte=dal, data__lte=al))
    ticket = list(TicketResto.objects.filter(data__gte=dal, data__lte=al))
    voluti = {}
    for q in QuadraturaGiornaliera.objects.filter(data__gte=dal, data__lte=al):
        lordo = (q.contanti_totali - q.fondo_cassa_iniziale
                 + sum((s.importo for s in spese if s.data == q.data), Decimal('0'))
                 + sum((t.importo for t in ticket if t.data == q.data), Decimal('0')))
        voluti[f'cassa|incassi|{q.data:%Y-%m-%d}'] = (q.data, lordo, 'Incassi in contanti del giorno', voci.get('N.INCASSI'))
    for s in spese:
        voluti[f'cassa|spesa|{s.pk}'] = (s.data, -s.importo, f'Spesa di cassa: {s.descrizione}',
                                         voci.get(VOCI_SPESE_CASSA.get(s.categoria, 'CF.ALTRI')))
    for t in ticket:
        voluti[f'cassa|ticket|{t.pk}'] = (t.data, -t.importo, f'Ticket di resto pagato{": " + t.nota if t.nota else ""}',
                                          voci.get('N.INCASSI'))

    scritti = 0
    with transaction.atomic():
        esistenti = {m.impronta: m for m in conto.movimenti.filter(origine='cassa', data__gte=dal, data__lte=al)}
        for impronta, (giorno, importo, descrizione, voce) in voluti.items():
            m = esistenti.pop(impronta, None)
            if m is None:
                MovimentoFinanziario.objects.create(conto=conto, data=giorno, importo=importo, descrizione=descrizione,
                                                    impronta=impronta, voce=voce, origine='cassa')
                scritti += 1
            elif (m.data, m.importo, m.descrizione) != (giorno, importo, descrizione):
                m.data, m.importo, m.descrizione = giorno, importo, descrizione
                m.save(update_fields=['data', 'importo', 'descrizione'])
                scritti += 1
        # spese o ticket cancellati nel frattempo; un incasso a zero si toglie
        for m in esistenti.values():
            m.delete()
        conto.movimenti.filter(origine='cassa', importo=0).delete()
    return scritti
