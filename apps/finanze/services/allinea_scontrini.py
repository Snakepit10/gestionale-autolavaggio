"""Allineamento della chiusura portali ai WashCycles degli scontrini.

Ogni cassa automatica dei portali stampa a fine giornata uno scontrino
con i WashCycles erogati dall'ultima chiusura (registrati nella
ChiusuraCassaAutomatica del giorno). Le due casse vengono chiuse in
momenti diversi e il loro orologio non coincide con quello WashTec,
quindi per ciascun portale si cerca la fine che da' esattamente i
WashCycles dello scontrino: tenendo fisso l'inizio (la fine del giorno
prima), la fine deve cadere tra l'N-esimo e l'(N+1)-esimo lavaggio; dentro
quell'intervallo si sceglie l'orario piu' vicino a quello attuale. La fine resta
sempre nella sera della giornata: se li' nessun orario combacia si propone
quello che ci va piu' vicino, segnalando lo scarto.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.finanze.models import ChiusuraCassaAutomatica, TransazionePortale

# Portale WashTec -> parola nel nome della cassa automatica
CASSA_PORTALE = {'A': 'azzurro', 'B': 'blu'}
# La chiusura degli scontrini cade la sera della giornata: la fine
# proposta resta tra quest'ora e mezzanotte
SERA_DALLE = time(16, 0)


def chiusure_casse_portali(data):
    """{'A': ChiusuraCassaAutomatica | None, 'B': ...} del giorno."""
    esito = {'A': None, 'B': None}
    for c in ChiusuraCassaAutomatica.objects.filter(data=data).select_related('cassa'):
        nome = c.cassa.nome.lower()
        for portale, parola in CASSA_PORTALE.items():
            if parola in nome:
                esito[portale] = c
    return esito


def washcycles_scontrini(data):
    """{'A': n, 'B': n} dalle chiusure casse automatiche del giorno
    (None se lo scontrino non e' registrato o senza WashCycles)."""
    return {p: (c.wash_cycles if c else None)
            for p, c in chiusure_casse_portali(data).items()}


def _sera(data):
    """Fascia in cui puo' cadere la chiusura della giornata."""
    return (timezone.make_aware(datetime.combine(data, SERA_DALLE)),
            timezone.make_aware(datetime.combine(data, time(23, 59, 59))))


def allinea(chiusura, scontrini):
    """Per ogni portale con scontrino: {'scontrino', 'da', 'fine',
    'fine_attuale', 'tra': (min, max), 'conteggio', 'scarto', 'errore'}.

    'fine' e' l'orario, nella sera della giornata, che fa combaciare il
    conteggio con lo scontrino; se nessun orario della sera ci riesce
    (lo scontrino conta un lavaggio che WashTec non ha, o viceversa) e'
    quello che ci va piu' vicino, con 'scarto' = conteggio - scontrino.
    """
    esito = {}
    sera_da, sera_a = _sera(chiusura.data)
    for portale, n in scontrini.items():
        if n is None:
            continue
        da, fine_attuale = chiusura.finestra(portale)
        lavaggi = list(TransazionePortale.objects
                       .filter(portale=portale, orario__gt=da, orario__lte=sera_a)
                       .order_by('orario', 'numero')
                       .values_list('orario', flat=True))
        dopo = (TransazionePortale.objects
                .filter(portale=portale, orario__gt=sera_a)
                .order_by('orario').values_list('orario', flat=True).first())
        voce = {'scontrino': n, 'da': da, 'fine_attuale': fine_attuale,
                'fine': None, 'tra': None, 'conteggio': None, 'scarto': 0, 'errore': ''}
        esito[portale] = voce
        if dopo is None and len(lavaggi) < n:
            voce['errore'] = (f'in archivio ci sono solo {len(lavaggi)} lavaggi dopo '
                              f'l\'inizio: importa le transazioni WashTec mancanti')
            continue
        # Ogni k = lavaggi contati ha un intervallo di fine possibile:
        # dal k-esimo lavaggio a un secondo prima del successivo, tagliato
        # sulla sera della giornata
        candidati = []
        for k in range(len(lavaggi) + 1):
            minimo = max(lavaggi[k - 1] if k else da, sera_da)
            successivo = lavaggi[k] if k < len(lavaggi) else dopo
            massimo = min(successivo - timedelta(seconds=1), sera_a) if successivo else sera_a
            if massimo < minimo:
                continue
            fine = min(max(fine_attuale, minimo), massimo).replace(microsecond=0)
            candidati.append((abs(k - n), abs((fine - fine_attuale).total_seconds()),
                              k, fine, minimo, massimo))
        if not candidati:
            voce['errore'] = 'nessun orario possibile nella sera della giornata'
            continue
        _, _, k, fine, minimo, massimo = min(candidati)
        voce.update({'fine': fine, 'tra': (minimo, massimo),
                     'conteggio': k, 'scarto': k - n})
    return esito
