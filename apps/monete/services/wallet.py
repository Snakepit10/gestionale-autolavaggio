"""Operazioni atomiche sul saldo monete.

UNICO punto di scrittura del saldo: ogni variazione passa da
accredita()/addebita(), che dentro una transazione bloccano la riga
saldo (select_for_update), la aggiornano e creano il movimento di
ledger con saldo_dopo. Cosi':
- niente race read-modify-write (il buco noto di gestisci_punti_fedelta
  sui punti fedelta' NON viene replicato qui);
- niente saldi negativi (check esplicito + PositiveIntegerField);
- niente doppioni: la chiave di idempotenza e' unique (parziale) sul
  movimento; un replay della stessa operazione solleva
  OperazioneDuplicataError e NON tocca il saldo.
"""
from django.db import IntegrityError, transaction

from apps.monete.models import MovimentoMoneta, SaldoMonete


class SaldoInsufficienteError(Exception):
    """Il cliente non ha abbastanza monete per l'addebito richiesto."""


class OperazioneDuplicataError(Exception):
    """Chiave di idempotenza gia' usata: l'operazione originale e' gia'
    andata a buon fine, il chiamante NON deve riprovare."""


def saldo_di(cliente) -> int:
    """Saldo corrente del cliente (0 se non ha ancora un portafoglio)."""
    riga = SaldoMonete.objects.filter(cliente=cliente).first()
    return riga.saldo if riga else 0


def _saldo_locked(cliente) -> SaldoMonete:
    """Riga saldo del cliente, creata se manca, bloccata per update.

    Va chiamata DENTRO transaction.atomic().
    """
    SaldoMonete.objects.get_or_create(cliente=cliente)
    return SaldoMonete.objects.select_for_update().get(cliente=cliente)


def _movimento(cliente, saldo_riga, monete, tipo, descrizione, **extra):
    """Crea il movimento traducendo il conflitto di idempotenza."""
    try:
        return MovimentoMoneta.objects.create(
            cliente=cliente,
            tipo=tipo,
            monete=monete,
            saldo_dopo=saldo_riga.saldo,
            descrizione=descrizione,
            **extra,
        )
    except IntegrityError as exc:
        raise OperazioneDuplicataError(
            f'Operazione gia\' registrata (chiave '
            f'{extra.get("chiave_idempotenza", "?")}).'
        ) from exc


def accredita(cliente, monete: int, tipo: str, descrizione: str, *,
              operatore=None, acquisto=None, importo=None,
              chiave_idempotenza: str = '') -> MovimentoMoneta:
    """Aggiunge monete al saldo del cliente e registra il movimento."""
    if monete < 1:
        raise ValueError('L\'accredito deve essere di almeno 1 moneta.')
    with transaction.atomic():
        riga = _saldo_locked(cliente)
        riga.saldo += monete
        riga.save(update_fields=['saldo', 'aggiornato_il'])
        return _movimento(
            cliente, riga, monete, tipo, descrizione,
            operatore=operatore, acquisto=acquisto, importo=importo,
            chiave_idempotenza=chiave_idempotenza,
        )


def addebita(cliente, monete: int, tipo: str, descrizione: str, *,
             nodo=None, impulsi=None, operatore=None,
             chiave_idempotenza: str = '') -> MovimentoMoneta:
    """Scala monete dal saldo del cliente e registra il movimento.

    Solleva SaldoInsufficienteError se il saldo non copre l'addebito.
    """
    if monete < 1:
        raise ValueError('L\'addebito deve essere di almeno 1 moneta.')
    with transaction.atomic():
        riga = _saldo_locked(cliente)
        if riga.saldo < monete:
            raise SaldoInsufficienteError(
                f'Saldo insufficiente: servono {monete} monete, '
                f'disponibili {riga.saldo}.')
        riga.saldo -= monete
        riga.save(update_fields=['saldo', 'aggiornato_il'])
        return _movimento(
            cliente, riga, -monete, tipo, descrizione,
            nodo=nodo, impulsi=impulsi, operatore=operatore,
            chiave_idempotenza=chiave_idempotenza,
        )
