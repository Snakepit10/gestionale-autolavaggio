"""Allineamento della chiusura portali ai WashCycles degli scontrini.

Ogni cassa automatica dei portali stampa a fine giornata uno scontrino
con i WashCycles erogati dall'ultima chiusura (registrati nella
ChiusuraCassaAutomatica del giorno). Le due casse vengono chiuse in
momenti diversi e il loro orologio non coincide con quello WashTec,
quindi per ciascun portale si cerca la fine che da' esattamente i
WashCycles dello scontrino: tenendo fisso l'inizio (la fine del giorno
prima), la fine deve cadere tra l'N-esimo e l'(N+1)-esimo lavaggio; dentro
quell'intervallo si sceglie l'orario piu' vicino a quello attuale.
"""
from datetime import timedelta

from apps.finanze.models import ChiusuraCassaAutomatica, TransazionePortale

# Portale WashTec -> parola nel nome della cassa automatica
CASSA_PORTALE = {'A': 'azzurro', 'B': 'blu'}


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


def allinea(chiusura, scontrini):
    """Per ogni portale con scontrino: {'scontrino', 'da', 'fine',
    'fine_attuale', 'tra': (min, max), 'errore'}. 'fine' e' l'orario che
    fa combaciare il conteggio (None con 'errore' se non si puo')."""
    esito = {}
    for portale, n in scontrini.items():
        if n is None:
            continue
        da, fine_attuale = chiusura.finestra(portale)
        lavaggi = list(TransazionePortale.objects
                       .filter(portale=portale, orario__gt=da)
                       .order_by('orario', 'numero')
                       .values_list('orario', flat=True)[:n + 1])
        voce = {'scontrino': n, 'da': da, 'fine_attuale': fine_attuale,
                'fine': None, 'tra': None, 'errore': ''}
        esito[portale] = voce
        if len(lavaggi) < n:
            voce['errore'] = (f'in archivio ci sono solo {len(lavaggi)} lavaggi dopo '
                              f'l\'inizio: importa le transazioni WashTec mancanti')
            continue
        minimo = lavaggi[n - 1] if n else da
        massimo = lavaggi[n] - timedelta(seconds=1) if len(lavaggi) > n else None
        if massimo is not None and massimo < minimo:
            voce['errore'] = ('due lavaggi nello stesso secondo a cavallo della '
                              'chiusura: impossibile separarli')
            continue
        fine = max(fine_attuale, minimo)
        if massimo is not None:
            fine = min(fine, massimo)
        voce['fine'] = fine.replace(microsecond=0)
        voce['tra'] = (minimo, massimo)
    return esito
