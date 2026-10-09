"""Allineamento della chiusura portali agli scontrini delle casse.

Ogni cassa automatica dei portali stampa a fine giornata uno scontrino
con WashCycles e vendite dall'ultima chiusura (registrati nella
ChiusuraCassaAutomatica del giorno). Le due casse vengono chiuse in
momenti diversi e il loro orologio non coincide con quello WashTec,
quindi per ciascun portale si cerca la fine della giornata: l'inizio e'
la fine del giorno prima, la fine cade tra due lavaggi consecutivi e
si sceglie prima per saldo (contanti WashTec a listino = vendita dello
scontrino), poi per numero di lavaggi (= WashCycles), poi per vicinanza
all'orario attuale. La fine resta sempre nella sera della giornata:
quello che non si riesce a far tornare viene segnalato come scarto.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.finanze.models import (PREZZI_PROGRAMMA_PORTALE, ChiusuraCassaAutomatica,
                                 TransazionePortale)

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


def inizio_continuo(chiusura, portale):
    """Inizio della giornata del portale: la fine della chiusura del giorno
    prima, se salvata (niente buchi ne' sovrapposizioni), altrimenti
    quello della chiusura."""
    from apps.finanze.models import ChiusuraPortali

    prec = ChiusuraPortali.objects.filter(data=chiusura.data - timedelta(days=1)).first()
    return prec.finestra(portale)[1] if prec else chiusura.finestra(portale)[0]


def allinea(chiusura, casse):
    """Per ogni portale con scontrino (casse = chiusure_casse_portali):
    {'scontrino', 'vendita', 'da', 'da_attuale', 'fine', 'fine_attuale',
    'tra': (min, max), 'conteggio', 'scarto', 'contanti', 'scarto_contanti',
    'errore'}.

    L'inizio e' la fine del giorno prima. La fine, nella sera della
    giornata, e' scelta per priorita':
    1. contanti dei lavaggi WashTec (a listino) uguali alla vendita
       contante + non contante dello scontrino (il saldo);
    2. numero di lavaggi uguale ai WashCycles dello scontrino;
    3. orario piu' vicino a quello attuale.
    'scarto' e 'scarto_contanti' dicono quanto resta se non si azzera.
    """
    esito = {}
    sera_da, sera_a = _sera(chiusura.data)
    for portale, cassa in casse.items():
        if cassa is None:
            continue
        n = cassa.wash_cycles
        vendita = cassa.vendita_totale
        da_attuale, fine_attuale = chiusura.finestra(portale)
        da = inizio_continuo(chiusura, portale)
        lavaggi = list(TransazionePortale.objects
                       .filter(portale=portale, orario__gt=da, orario__lte=sera_a)
                       .order_by('orario', 'numero')
                       .values_list('orario', 'programma', 'origine'))
        dopo = (TransazionePortale.objects
                .filter(portale=portale, orario__gt=sera_a)
                .order_by('orario').values_list('orario', flat=True).first())
        voce = {'scontrino': n, 'vendita': vendita, 'da': da, 'da_attuale': da_attuale,
                'fine_attuale': fine_attuale, 'fine': None, 'tra': None,
                'conteggio': None, 'scarto': 0, 'contanti': None, 'scarto_contanti': 0,
                'errore': ''}
        esito[portale] = voce
        if dopo is None and n is not None and len(lavaggi) < n:
            voce['errore'] = (f'in archivio ci sono solo {len(lavaggi)} lavaggi dopo '
                              "l'inizio: importa le transazioni WashTec mancanti")
            continue
        # Ogni k = lavaggi contati ha un intervallo di fine possibile:
        # dal k-esimo lavaggio a un secondo prima del successivo, tagliato
        # sulla sera della giornata
        candidati = []
        contanti = Decimal('0.00')
        for k in range(len(lavaggi) + 1):
            if k and lavaggi[k - 1][2] == 'contanti':
                contanti += PREZZI_PROGRAMMA_PORTALE[lavaggi[k - 1][1]]
            minimo = max(lavaggi[k - 1][0] if k else da, sera_da)
            successivo = lavaggi[k][0] if k < len(lavaggi) else dopo
            massimo = min(successivo - timedelta(seconds=1), sera_a) if successivo else sera_a
            if massimo < minimo:
                continue
            fine = min(max(fine_attuale, minimo), massimo).replace(microsecond=0)
            candidati.append((abs(contanti - vendita),
                              abs(k - n) if n is not None else 0,
                              abs((fine - fine_attuale).total_seconds()),
                              k, contanti, fine, minimo, massimo))
        if not candidati:
            voce['errore'] = 'nessun orario possibile nella sera della giornata'
            continue
        _, _, _, k, contanti, fine, minimo, massimo = min(candidati)
        voce.update({'fine': fine, 'tra': (minimo, massimo), 'conteggio': k,
                     'scarto': (k - n) if n is not None else 0,
                     'contanti': contanti, 'scarto_contanti': contanti - vendita})
    return esito
