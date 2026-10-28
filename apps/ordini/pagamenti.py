"""Data dei pagamenti ("Pagato il").

Il pagamento conta nella quadratura del giorno in cui il cliente ha pagato:
chi lo registra in ritardo indica quel giorno. Una data diversa da adesso
lascia traccia nella nota (quando e da chi e' stato registrato).
"""
from datetime import datetime, timedelta

from django.utils import timezone

FORMATI = ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M')
MARGINE_FUTURO = timedelta(minutes=5)


def leggi_pagato_il(valore):
    """(datetime, errore) da un 'YYYY-MM-DDTHH:MM' del browser; vuoto = adesso."""
    if not valore:
        return timezone.now(), None
    for formato in FORMATI:
        try:
            quando = timezone.make_aware(datetime.strptime(str(valore).strip(), formato))
            break
        except ValueError:
            continue
    else:
        return None, 'Data del pagamento non valida.'
    errore = controlla_pagato_il(quando)
    return (None, errore) if errore else (quando, None)


def controlla_pagato_il(quando):
    if quando and quando > timezone.now() + MARGINE_FUTURO:
        return 'La data del pagamento non può essere nel futuro.'
    return None


def nota_registrazione(nota, quando, utente):
    """Aggiunge alla nota quando e da chi e' stato registrato un pagamento
    con una data diversa da adesso."""
    adesso = timezone.now()
    if quando is None or abs(adesso - quando) < timedelta(minutes=10):
        return nota or ''
    chi = (utente.get_full_name() or utente.username) if utente else '-'
    traccia = f"Registrato il {timezone.localtime(adesso):%d/%m/%Y %H:%M} da {chi}"
    return f"{nota} - {traccia}" if nota else traccia
