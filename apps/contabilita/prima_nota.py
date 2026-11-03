"""Prima nota: abbinamento dei pagamenti alle scadenze delle fatture,
giroconti tra conti e movimenti della cassa contanti ricavati dalle
quadrature giornaliere."""
import re
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .models import MovimentoFinanziario, ScadenzaFattura, VoceConto

ZERO = Decimal('0')
GIORNI_ABBINAMENTO = 45
# Bonifico con la commissione dentro: si propone se paga al massimo questo
# in piu' della scadenza e la descrizione ha il nome del fornitore
COMMISSIONE_MAX = Decimal('5.00')
VOCE_COMMISSIONI = 'CF.BANCA'
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
    """Uscite non ancora abbinate con una scadenza aperta entro 45 giorni:
    - dello stesso importo (al centesimo);
    - oppure piu' alte al massimo di COMMISSIONE_MAX, se la descrizione ha
      il nome del fornitore: la differenza e' la commissione del bonifico.
    Vince l'importo esatto, poi il nome del fornitore, poi la piu' vicina."""
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
                proposte[mov.pk] = {'movimento': mov, 'scadenza': s, 'giorni': giorni, 'nome': nome,
                                    'commissione': commissione}
    return sorted(proposte.values(), key=lambda p: (p['movimento'].data, p['movimento'].pk))


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
    return contanti


def _abbina(movimento, scadenze, differenza, voce_differenza):
    with transaction.atomic():
        for s in scadenze:
            s.movimento, s.pagata_il = movimento, movimento.data
            s.save(update_fields=['movimento', 'pagata_il'])
        movimento.voce = VoceConto.objects.get(codice='N.FORNITORI')
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
                movimento.delete()                    # uscita di cassa creata col pagamento parziale
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
