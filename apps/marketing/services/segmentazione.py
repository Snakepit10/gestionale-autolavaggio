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

from django.db.models import Count, Max, Min
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


def segmenta_clienti(cfg: ImpostazioniMarketing | None = None) -> RisultatoSegmentazione:
    """Classifica tutti i clienti con almeno 1 lavaggio completato."""
    cfg = cfg or ImpostazioniMarketing.get_solo()
    oggi = timezone.now()

    # Aggregato per cliente: n. lavaggi, primo e ultimo.
    # La frequenza media si stima come (ultimo - primo) / (n - 1):
    # non serve la lista completa delle date, bastano gli estremi.
    stats = (
        Cliente.objects
        .filter(ordine__stato='completato')
        .annotate(
            n_lavaggi=Count('ordine', distinct=True),
            primo=Min('ordine__data_ora'),
            ultimo=Max('ordine__data_ora'),
        )
    )

    out = RisultatoSegmentazione()
    soglia_dormiente = timedelta(days=cfg.giorni_dormiente)

    for c in stats:
        giorni_da_ultimo = (oggi - c.ultimo).days

        if c.n_lavaggi == 1:
            freq = None
        else:
            span = (c.ultimo - c.primo).days
            freq = span / (c.n_lavaggi - 1) if span > 0 else 1.0

        cs = ClienteSegmentato(
            cliente=c,
            ultimo_lavaggio=c.ultimo,
            totale_lavaggi=c.n_lavaggi,
            frequenza_media_giorni=freq,
            giorni_da_ultimo=giorni_da_ultimo,
        )

        # Ordine di valutazione: one_shot -> dormiente -> rallentamento
        # -> attivo. Un one-shot vecchissimo resta one-shot (non
        # dormiente): la comunicazione di riattivazione per chi non e'
        # MAI tornato e' diversa da quella per un habitue' sparito.
        if c.n_lavaggi == 1:
            out.one_shot.append(cs)
        elif oggi - c.ultimo > soglia_dormiente:
            out.dormienti.append(cs)
        elif freq is not None and giorni_da_ultimo > freq + cfg.giorni_rallentamento_delta:
            out.rallentamento.append(cs)
        else:
            out.attivi.append(cs)

    # Ordina ogni segmento per "urgenza" (piu' giorni dall'ultimo prima)
    for lst in (out.attivi, out.rallentamento, out.dormienti, out.one_shot):
        lst.sort(key=lambda x: -x.giorni_da_ultimo)

    return out
