"""Stato del percorso di un veicolo (sola lettura, nessuna scrittura).

Un item di livello e' completato se sul libretto esiste un evento del
suo tipo servizio, oppure un evento self_service quando l'item ha
soddisfatto_da_self=True (es. lavaggio esterno). Il livello N+1 e'
bloccato finche' il livello N non ha tutti gli item completati.
"""
from dataclasses import dataclass, field

from apps.garage.models import LivelloPercorso, Percorso, Veicolo

SLUG_SELF = 'self_service'


@dataclass
class ItemStato:
    slug: str
    nome: str
    completato: bool
    soddisfatto_da_self: bool


@dataclass
class LivelloStato:
    livello: LivelloPercorso
    items: list = field(default_factory=list)
    stato: str = 'bloccato'   # bloccato | attivo | completato
    n_fatti: int = 0
    n_totali: int = 0

    @property
    def percentuale(self) -> int:
        return int(self.n_fatti / self.n_totali * 100) if self.n_totali else 0


@dataclass
class StatoPercorso:
    livelli: list = field(default_factory=list)
    corrente: LivelloStato | None = None   # None quando nel Club
    nel_club: bool = False

    @property
    def label_livello(self) -> str:
        if self.nel_club:
            return 'Club Auto Perfetta'
        if self.corrente is None:
            return '—'
        return f'Livello {self.corrente.livello.numero} · {self.corrente.livello.nome}'

    def prossimo_item(self) -> ItemStato | None:
        """Primo servizio non completato del livello corrente."""
        if self.corrente is None:
            return None
        for it in self.corrente.items:
            if not it.completato:
                return it
        return None


def stato_percorso(veicolo: Veicolo) -> StatoPercorso:
    percorso = veicolo.percorso or Percorso.standard()
    out = StatoPercorso()
    if percorso is None:
        return out

    slugs_fatti = set(
        veicolo.eventi.values_list('tipo_servizio__slug', flat=True))
    ha_self = SLUG_SELF in slugs_fatti

    precedenti_completi = True
    for livello in (percorso.livelli.order_by('numero')
                    .prefetch_related('items__tipo_servizio')):
        ls = LivelloStato(livello=livello)
        for item in livello.items.all():
            fatto = (item.tipo_servizio.slug in slugs_fatti
                     or (item.soddisfatto_da_self and ha_self))
            ls.items.append(ItemStato(
                slug=item.tipo_servizio.slug,
                nome=item.tipo_servizio.nome,
                completato=fatto,
                soddisfatto_da_self=item.soddisfatto_da_self,
            ))
        ls.n_totali = len(ls.items)
        ls.n_fatti = sum(1 for i in ls.items if i.completato)
        completo = ls.n_totali > 0 and ls.n_fatti == ls.n_totali
        if completo:
            ls.stato = 'completato'
        elif precedenti_completi:
            ls.stato = 'attivo'
            if out.corrente is None:
                out.corrente = ls
        else:
            ls.stato = 'bloccato'
        precedenti_completi = precedenti_completi and completo
        out.livelli.append(ls)

    out.nel_club = bool(out.livelli) and all(
        l.stato == 'completato' for l in out.livelli)
    return out
