"""Scadenze dei trattamenti di protezione (rinnovi nel Club).

Per ogni tipo servizio con validita_giorni > 0 registrato sul
libretto: la validita' parte dall'ULTIMO evento di quel tipo. Calcoli
su date locali (Europe/Rome, timezone di progetto).
"""
from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from apps.garage.models import TipoServizioGarage, Veicolo


@dataclass
class Trattamento:
    tipo: TipoServizioGarage
    ultimo_il: date
    scade_il: date
    giorni_rimanenti: int   # negativo = scaduto da N giorni

    @property
    def stato(self) -> str:
        if self.giorni_rimanenti < 0:
            return 'scaduto'
        if self.giorni_rimanenti <= 30:
            return 'in_scadenza'
        return 'attivo'


def trattamenti_veicolo(veicolo: Veicolo) -> list[Trattamento]:
    """Stato dei trattamenti del veicolo, i piu' urgenti prima."""
    oggi = timezone.localtime(timezone.now()).date()
    out = []
    # ultimo evento per ogni tipo con validita'
    eventi = (veicolo.eventi
              .filter(tipo_servizio__validita_giorni__gt=0)
              .select_related('tipo_servizio')
              .order_by('tipo_servizio_id', '-data'))
    visti = set()
    for e in eventi:
        if e.tipo_servizio_id in visti:
            continue
        visti.add(e.tipo_servizio_id)
        ultimo = timezone.localtime(e.data).date()
        scade = ultimo + timedelta(days=e.tipo_servizio.validita_giorni)
        out.append(Trattamento(
            tipo=e.tipo_servizio, ultimo_il=ultimo, scade_il=scade,
            giorni_rimanenti=(scade - oggi).days,
        ))
    out.sort(key=lambda t: t.giorni_rimanenti)
    return out


def da_rinnovare(veicolo: Veicolo) -> list[Trattamento]:
    return [t for t in trattamenti_veicolo(veicolo) if t.stato != 'attivo']
