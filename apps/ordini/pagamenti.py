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


# --- Controllo dei pagamenti di un ordine -----------------------------------
# Due pagamenti sullo stesso ordine possono essere giusti (parte contanti e
# parte carta) o un doppione: l'operatore sceglie quale tenere o conferma
# che vanno bene tutti. Pagato piu' del totale e' sempre un errore.

def pagato_in_piu(ordine):
    """Quanto l'ordine risulta pagato oltre il suo totale (0 se nulla)."""
    from decimal import Decimal
    eccesso = (ordine.importo_pagato or Decimal('0')) - (ordine.totale_finale or Decimal('0'))
    return eccesso if eccesso > Decimal('0.005') else Decimal('0')


def segna_controllo(ordine):
    """Imposta su ordine in_piu, pagamenti_doppi e da_controllare (usa i
    pagamenti gia' caricati, se prefetchati)."""
    n = len(ordine.pagamenti.all())
    ordine.in_piu = pagato_in_piu(ordine)
    ordine.pagamenti_doppi = n > max(1, ordine.pagamenti_confermati)
    ordine.da_controllare = bool(ordine.in_piu) or ordine.pagamenti_doppi
    return ordine.da_controllare


def ordini_da_controllare(ordini):
    """Gli ordini con piu' pagamenti non confermati o pagati in piu'."""
    return [o for o in ordini if segna_controllo(o)]


def _chi(utente):
    return (utente.get_full_name() or utente.username) if utente else '-'


def _descrivi(p):
    ora = timezone.localtime(p.data_pagamento)
    return f"€{p.importo} {p.get_metodo_display().lower()} delle {ora:%H:%M} ({_chi(p.operatore)})"


def _traccia(pagamento, testo, utente):
    testo = f"{testo} il {timezone.localtime():%d/%m/%Y %H:%M} da {_chi(utente)}"
    pagamento.nota = f"{pagamento.nota} - {testo}" if pagamento.nota else testo


def tieni_solo(pagamento, utente):
    """Tiene questo pagamento e toglie gli altri dell'ordine; la traccia di
    quelli tolti resta nella nota del pagamento tenuto."""
    altri = list(pagamento.ordine.pagamenti.exclude(pk=pagamento.pk))
    if not altri:
        return 0
    _traccia(pagamento, 'Tolti ' + ', '.join(_descrivi(p) for p in altri), utente)
    pagamento.save(update_fields=['nota'])
    for p in altri:
        p.delete()   # i signal ricalcolano importo e stato dell'ordine
    return len(altri)


def togli(pagamento, utente):
    """Toglie un pagamento; la traccia va in un altro pagamento dell'ordine."""
    resta = pagamento.ordine.pagamenti.exclude(pk=pagamento.pk).order_by('data_pagamento').first()
    if resta:
        _traccia(resta, 'Tolto ' + _descrivi(pagamento), utente)
        resta.save(update_fields=['nota'])
    pagamento.delete()


def correggi_in_piu(pagamento, utente):
    """Riduce il pagamento di quanto l'ordine risulta pagato in piu'.
    None se l'ordine non e' pagato in piu' o il pagamento non basta."""
    eccesso = pagato_in_piu(pagamento.ordine)
    if not eccesso or pagamento.importo - eccesso <= 0:
        return None
    _traccia(pagamento, f"Importo corretto da €{pagamento.importo}", utente)
    pagamento.importo -= eccesso
    pagamento.save(update_fields=['importo', 'nota'])
    return pagamento.importo
