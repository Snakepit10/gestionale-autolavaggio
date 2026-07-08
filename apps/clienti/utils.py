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
