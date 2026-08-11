"""Segmentazione automatica dei clienti in base allo storico lavaggi.

Quattro segmenti mutuamente esclusivi:

- one_shot:       1 solo lavaggio completato, mai tornati
- dormienti:      ultimo lavaggio > giorni_dormiente fa (default 120)
- rallentamento:  giorni dall'ultimo lavaggio > media storica del
                  cliente + giorni_rallentamento_delta (default +30)
- attivi:         tutti gli altri con >= 2 lavaggi (frequenza regolare)

I clienti SENZA lavaggi completati non compaiono in nessun segmento
(non sono clienti "storici" da riattivare: tipicamente anagrafiche
create ma mai concretizzate, o ordini solo annullati).

Il calcolo e' al volo: 1 query aggregata su Ordine + loop in Python.
Con migliaia di clienti resta sotto il secondo. Scelto rispetto alla
tabella materializzata per evitare stale data e una moving part in
piu' (cron di refresh); si riconsidera se il volume cresce di 10x.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from apps.clienti.models import Cliente
from apps.marketing.models import ImpostazioniMarketing


@dataclass
class ClienteSegmentato:
    cliente: Cliente
    ultimo_lavaggio: 'timezone.datetime'
    totale_lavaggi: int
    frequenza_media_giorni: float | None  # None se 1 solo lavaggio
    giorni_da_ultimo: int
    totale_speso: Decimal = Decimal('0')
    spesa_media: Decimal = Decimal('0')

    @property
    def telefono(self):
        return self.cliente.telefono

    @property
    def nome_completo(self):
        return self.cliente.nome_completo


@dataclass
class RisultatoSegmentazione:
    attivi: list[ClienteSegmentato] = field(default_factory=list)
    rallentamento: list[ClienteSegmentato] = field(default_factory=list)
    dormienti: list[ClienteSegmentato] = field(default_factory=list)
    one_shot: list[ClienteSegmentato] = field(default_factory=list)

    def as_dict(self):
        return {
            'attivi': self.attivi,
            'rallentamento': self.rallentamento,
            'dormienti': self.dormienti,
            'one_shot': self.one_shot,
        }

    def get(self, chiave):
        return self.as_dict().get(chiave, [])


SEGMENTI_LABEL = {
    'attivi': 'Attivi regolari',
    'rallentamento': 'In rallentamento',
    'dormienti': 'Dormienti',
    'one_shot': 'One-shot',
}


def statistiche_clienti(alla_data=None) -> list[ClienteSegmentato]:
    """Statistiche per-cliente (una query aggregata + loop Python).

    Base comune dei segmenti automatici e di quelli personalizzati:
    n. lavaggi, primo/ultimo, frequenza media stimata come
    (ultimo - primo) / (n - 1), spesa totale e media a lavaggio.
    Solo clienti con almeno 1 lavaggio completato.

    `alla_data`: fotografia storica — considera solo gli ordini fino a
    quella data e calcola giorni_da_ultimo rispetto ad essa. Usata dai
    trend KPI dei segmenti (la segmentazione dipende solo dallo storico
    ordini, quindi il passato e' ricostruibile esattamente).
    """
    from django.db.models import Q

    oggi = alla_data or timezone.now()
    filtro = Q(ordine__stato='completato')
    if alla_data is not None:
        filtro &= Q(ordine__data_ora__lte=alla_data)
    stats = (
        Cliente.objects
        .filter(filtro)
        .annotate(
            n_lavaggi=Count('ordine', distinct=True, filter=filtro),
            primo=Min('ordine__data_ora', filter=filtro),
            ultimo=Max('ordine__data_ora', filter=filtro),
            speso=Sum('ordine__totale_finale', filter=filtro),
        )
    )

    out = []
    for c in stats:
        if not c.n_lavaggi or c.ultimo is None:
            continue  # nessun lavaggio entro alla_data
        if c.n_lavaggi == 1:
            freq = None
        else:
            span = (c.ultimo - c.primo).days
            freq = span / (c.n_lavaggi - 1) if span > 0 else 1.0
        speso = c.speso or Decimal('0')
        out.append(ClienteSegmentato(
            cliente=c,
            ultimo_lavaggio=c.ultimo,
            totale_lavaggi=c.n_lavaggi,
            frequenza_media_giorni=freq,
            giorni_da_ultimo=(oggi - c.ultimo).days,
            totale_speso=speso,
            spesa_media=(speso / c.n_lavaggi).quantize(Decimal('0.01'))
                        if c.n_lavaggi else Decimal('0'),
        ))
    return out


def segmenta_clienti(cfg: ImpostazioniMarketing | None = None,
                     stats: list[ClienteSegmentato] | None = None,
                     adesso=None) -> RisultatoSegmentazione:
    """Classifica tutti i clienti con almeno 1 lavaggio completato.

    `adesso`: riferimento temporale per le soglie (deve combaciare con
    l'`alla_data` usata per calcolare `stats` nei trend storici).
    """
    cfg = cfg or ImpostazioniMarketing.get_solo()
    oggi = adesso or timezone.now()
    stats = stats if stats is not None else statistiche_clienti()

    out = RisultatoSegmentazione()
    soglia_dormiente = timedelta(days=cfg.giorni_dormiente)

    for cs in stats:
        # Ordine di valutazione: one_shot -> dormiente -> rallentamento
        # -> attivo. Un one-shot vecchissimo resta one-shot (non
        # dormiente): la comunicazione di riattivazione per chi non e'
        # MAI tornato e' diversa da quella per un habitue' sparito.
        freq = cs.frequenza_media_giorni
        if cs.totale_lavaggi == 1:
            out.one_shot.append(cs)
        elif oggi - cs.ultimo_lavaggio > soglia_dormiente:
            out.dormienti.append(cs)
        elif freq is not None and cs.giorni_da_ultimo > freq + cfg.giorni_rallentamento_delta:
            out.rallentamento.append(cs)
        else:
            out.attivi.append(cs)

    # Ordina ogni segmento per "urgenza" (piu' giorni dall'ultimo prima)
    for lst in (out.attivi, out.rallentamento, out.dormienti, out.one_shot):
        lst.sort(key=lambda x: -x.giorni_da_ultimo)

    return out


def filtra_segmento_personalizzato(segmento, stats=None) -> list[ClienteSegmentato]:
    """Applica i criteri di un SegmentoPersonalizzato alle statistiche.

    Criteri vuoti = non filtrano; quelli attivi si combinano in AND.
    I filtri sulla frequenza escludono i clienti con un solo lavaggio
    (frequenza indefinita).
    """
    stats = stats if stats is not None else statistiche_clienti()
    out = []
    for cs in stats:
        if segmento.tipo_cliente and cs.cliente.tipo != segmento.tipo_cliente:
            continue
        if segmento.lavaggi_min is not None and cs.totale_lavaggi < segmento.lavaggi_min:
            continue
        if segmento.lavaggi_max is not None and cs.totale_lavaggi > segmento.lavaggi_max:
            continue
        if segmento.giorni_ultimo_min is not None and cs.giorni_da_ultimo < segmento.giorni_ultimo_min:
            continue
        if segmento.giorni_ultimo_max is not None and cs.giorni_da_ultimo > segmento.giorni_ultimo_max:
            continue
        if segmento.frequenza_min is not None or segmento.frequenza_max is not None:
            if cs.frequenza_media_giorni is None:
                continue
            if segmento.frequenza_min is not None and cs.frequenza_media_giorni < segmento.frequenza_min:
                continue
            if segmento.frequenza_max is not None and cs.frequenza_media_giorni > segmento.frequenza_max:
                continue
        if segmento.spesa_totale_min is not None and cs.totale_speso < segmento.spesa_totale_min:
            continue
        if segmento.spesa_totale_max is not None and cs.totale_speso > segmento.spesa_totale_max:
            continue
        if segmento.spesa_media_min is not None and cs.spesa_media < segmento.spesa_media_min:
            continue
        if segmento.spesa_media_max is not None and cs.spesa_media > segmento.spesa_media_max:
            continue
        out.append(cs)
    out.sort(key=lambda x: -x.giorni_da_ultimo)
    return out
