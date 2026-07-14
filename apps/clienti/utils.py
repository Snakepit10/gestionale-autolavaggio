"""Utility condivise per l'anagrafica clienti.

Normalizzazione telefoni in E.164 e ricerca cliente per numero,
indipendenti dal formato con cui il numero e' stato digitato
(spazi, trattini, prefisso 00/+39, ecc.).
"""
from .models import Cliente


def normalizza_telefono(raw: str | None) -> str | None:
    """Numero in E.164 (+393331234567) o None se non parsabile.

    Riusa il normalizzatore gia' presente nel modulo WhatsApp
    (phonenumbers con default_country='IT').
    """
    from apps.clients.whatsapp import _to_e164
    return _to_e164(raw)


SOGLIA_SOMIGLIANZA_NOMI = 0.90


def somiglianza_nomi(nome1, cognome1, nome2, cognome2) -> float:
    """Somiglianza [0..1] tra due nominativi, insensibile a maiuscole,
    spazi extra e all'inversione nome/cognome (in cassa capita spesso
    'Rossi Mario' al posto di 'Mario Rossi')."""
    from difflib import SequenceMatcher

    def _norm(*parti):
        return ' '.join(' '.join((p or '') for p in parti).strip().lower().split())

    a = _norm(nome1, cognome1)
    if not a:
        return 0.0
    dritto = _norm(nome2, cognome2)
    invertito = _norm(cognome2, nome2)
    if not dritto:
        return 0.0
    return max(
        SequenceMatcher(None, a, dritto).ratio(),
        SequenceMatcher(None, a, invertito).ratio(),
    )


def valuta_collegamento_telefono(telefono, nome, cognome, escludi_pk=None):
    """Decide cosa fare quando una registrazione indica `telefono`.

    Ritorna (esito, cliente_esistente):
    - 'libero':           nessun cliente con quel numero -> procedi normale
    - 'collega':          scheda esistente SENZA account e nominativo
                          somigliante (>= SOGLIA_SOMIGLIANZA_NOMI) ->
                          collegare l'account a quella scheda
    - 'occupato':         scheda gia' collegata a un altro account online
    - 'verifica_fallita': scheda esistente ma il nominativo non combacia
                          -> invitare a chiamare/scrivere per lo sblocco
    """
    esistente = trova_cliente_per_telefono(telefono, escludi_pk=escludi_pk)
    if esistente is None:
        return 'libero', None
    if esistente.user_id:
        return 'occupato', esistente
    if somiglianza_nomi(nome, cognome, esistente.nome, esistente.cognome) >= SOGLIA_SOMIGLIANZA_NOMI:
        return 'collega', esistente
    return 'verifica_fallita', esistente


def trova_cliente_per_telefono(raw: str | None, escludi_pk=None):
    """Primo Cliente il cui telefono normalizza allo stesso E.164.

    None se il numero non e' parsabile o nessun cliente matcha.
    `escludi_pk` esclude un cliente (es. se stesso durante una modifica).

    Loop in Python sull'anagrafica: stesso pattern di
    _match_cliente_da_telefono (apps/api/views.py). Con migliaia di
    clienti resta rapido; se la rubrica cresce di un ordine di
    grandezza va aggiunto un campo telefono_e164 indicizzato.
    """
    target = normalizza_telefono(raw)
    if not target:
        return None
    qs = Cliente.objects.exclude(telefono='').only('id', 'telefono')
    if escludi_pk:
        qs = qs.exclude(pk=escludi_pk)
    for c in qs:
        if normalizza_telefono(c.telefono) == target:
            return c
    return None
