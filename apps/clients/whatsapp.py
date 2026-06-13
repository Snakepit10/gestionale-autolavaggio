"""Notifiche WhatsApp via Meta Cloud API.

Wrapper paralleli a `notifications.py` (email). Strategia:
- Tutte le chiamate sono fire-and-forget in daemon thread (come
  `apps/api/notify.py::_send_in_background`): la view ritorna entro
  pochi ms anche se Meta risponde lento o e' down.
- Senza env vars Meta configurate, le funzioni ritornano False senza
  effetti; il chiamante (in `notifications.py::notifica_*`) cade su
  email come fallback.

Tutti i messaggi outbound usano Template HSM Meta-approvati: senza
una conversazione aperta dal cliente nelle ultime 24h, WhatsApp
permette solo template. I nomi template sono in settings
(META_WA_TEMPLATE_*), i parametri sono sempre stringhe.

Setup esterno: docs/WHATSAPP_SETUP.md
"""
import logging
import threading

import phonenumbers
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_GRAPH_URL = 'https://graph.facebook.com'
_REQUEST_TIMEOUT = 8  # secondi


def _to_e164(phone: str | None, default_country: str = 'IT') -> str | None:
    """Normalizza un numero in formato E.164 (+393792337051).

    Ritorna None se il parsing fallisce o il numero non e' valido.
    Usa phonenumbers per gestire spazi, trattini, prefisso 00, ecc.
    """
    if not phone:
        return None
    try:
        parsed = phonenumbers.parse(phone.strip(), default_country)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return None


def _whatsapp_target(prenotazione) -> str | None:
    """Determina il numero WhatsApp di destinazione per una prenotazione.

    Priorita': telefono_contatto sulla Prenotazione (per booking
    anonimi) > cliente.telefono. Ritorna numero E.164 o None.
    """
    raw = getattr(prenotazione, 'telefono_contatto', '') or ''
    if not raw:
        cliente = getattr(prenotazione, 'cliente', None)
        if cliente:
            raw = getattr(cliente, 'telefono', '') or ''
    return _to_e164(raw)


def _send_template_blocking(to_e164: str, template_name: str, params: list[str]) -> bool:
    """Invio sincrono di un template Meta (chiamato dal thread daemon).

    `params` sono i valori per {{1}}, {{2}}, ... del body template.
    Tutti i parametri sono stringhe; tronca a 60 char ognuno per
    rispettare i limiti Meta sul body parameter (1024 totali).
    """
    if not settings.WHATSAPP_ENABLED:
        return False
    url = (
        f"{_GRAPH_URL}/{settings.META_WHATSAPP_API_VERSION}"
        f"/{settings.META_WHATSAPP_PHONE_ID}/messages"
    )
    headers = {
        'Authorization': f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }
    # Test mode: skippa i parametri body per compatibilita' con template
    # Meta pre-approvati senza variabili (es. hello_world). Vedi
    # settings.META_WA_OMIT_BODY_PARAMS.
    if settings.META_WA_OMIT_BODY_PARAMS:
        body_params = []
    else:
        body_params = [
            {'type': 'text', 'text': (str(p) if p is not None else '')[:60]}
            for p in params
        ]
    payload = {
        'messaging_product': 'whatsapp',
        'to': to_e164.lstrip('+'),  # Meta vuole solo cifre, no +
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': settings.META_WHATSAPP_TEMPLATE_LANG},
            'components': [
                {'type': 'body', 'parameters': body_params}
            ] if body_params else [],
        },
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(
                'WhatsApp send fallito (%s) to=%s template=%s: %s',
                r.status_code, to_e164, template_name, r.text[:300],
            )
            return False
        logger.info('WhatsApp inviato to=%s template=%s', to_e164, template_name)
        return True
    except requests.RequestException as e:
        logger.warning(
            'WhatsApp request error to=%s template=%s: %s',
            to_e164, template_name, e,
        )
        return False


def _send_text_blocking(to_e164: str, text: str) -> tuple[bool, str]:
    """Invia un messaggio di testo libero (non template) — sincrono.

    Funziona solo entro la "finestra di servizio" di 24h dall'ultimo
    messaggio del cliente; oltre, Meta restituisce errore
    "re-engagement required" (132047) e bisogna usare un template.

    Usato dalla view di risposta nell'inbox: vogliamo riportare in UI
    l'esito immediatamente, quindi semantica sincrona (no thread).

    Ritorna `(success, wa_message_id_o_errore)`:
    - successo: True, id messaggio Meta (es. "wamid.HBg...")
    - fallimento: False, descrizione errore (es. "400 132047 ...")
    """
    if not settings.WHATSAPP_ENABLED:
        return False, 'WHATSAPP_ENABLED=False'
    url = (
        f"{_GRAPH_URL}/{settings.META_WHATSAPP_API_VERSION}"
        f"/{settings.META_WHATSAPP_PHONE_ID}/messages"
    )
    headers = {
        'Authorization': f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }
    payload = {
        'messaging_product': 'whatsapp',
        'to': to_e164.lstrip('+'),
        'type': 'text',
        'text': {'body': (text or '')[:4096]},
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(
                'WhatsApp text fallito (%s) to=%s: %s',
                r.status_code, to_e164, r.text[:300],
            )
            return False, f'HTTP {r.status_code}: {r.text[:200]}'
        data = r.json()
        wa_id = ''
        try:
            wa_id = data['messages'][0]['id']
        except (KeyError, IndexError, TypeError):
            pass
        logger.info('WhatsApp text inviato to=%s id=%s', to_e164, wa_id)
        return True, wa_id
    except requests.RequestException as e:
        logger.warning('WhatsApp text request error to=%s: %s', to_e164, e)
        return False, str(e)


def _send_template(to_e164: str, template_name: str, params: list[str]) -> bool:
    """Fire-and-forget: avvia thread daemon e ritorna subito True.

    True == thread avviato. La response di Meta arriva nel log; non
    aspettiamo qui per non bloccare la view HTTP. Se vuoi semantica
    sync usa direttamente `_send_template_blocking`.
    """
    thread = threading.Thread(
        target=_send_template_blocking,
        args=(to_e164, template_name, params),
        daemon=True,
    )
    thread.start()
    return True


# ===========================================================================
# Wrapper "business" — 5 notifiche prenotazione
# ===========================================================================

def _data_ora(prenotazione) -> tuple[str, str]:
    data = prenotazione.slot.data.strftime('%d/%m/%Y')
    ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    return data, ora


def _nome_cliente(prenotazione) -> str:
    cliente = getattr(prenotazione, 'cliente', None)
    if not cliente:
        return 'Cliente'
    nome = (cliente.nome or cliente.cognome or '').strip()
    return nome or 'Cliente'


def whatsapp_prenotazione_ricevuta(prenotazione) -> bool:
    to = _whatsapp_target(prenotazione)
    if not to:
        return False
    nome = _nome_cliente(prenotazione)
    data, ora = _data_ora(prenotazione)
    servizi = ', '.join(s.titolo for s in prenotazione.servizi.all())[:60]
    # Template Meta approvato ha 4 variabili:
    # {{1}}=nome, {{2}}=data, {{3}}=ora, {{4}}=servizi
    # (il codice prenotazione e' stato rimosso dal testo approvato)
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_RICEVUTA,
        [nome, data, ora, servizi],
    )


def whatsapp_prenotazione_confermata(prenotazione) -> bool:
    to = _whatsapp_target(prenotazione)
    if not to:
        return False
    nome = _nome_cliente(prenotazione)
    data, ora = _data_ora(prenotazione)
    # Template approvato: 3 variabili {{1}}=nome, {{2}}=data, {{3}}=ora
    # (codice prenotazione non presente nel testo approvato)
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_CONFERMATA,
        [nome, data, ora],
    )


def whatsapp_prenotazione_rifiutata(prenotazione, motivo: str = '') -> bool:
    to = _whatsapp_target(prenotazione)
    if not to:
        return False
    nome = _nome_cliente(prenotazione)
    data, ora = _data_ora(prenotazione)
    motivo = (motivo or 'Slot non disponibile').strip()
    # Template approvato: 4 variabili
    # {{1}}=nome, {{2}}=data, {{3}}=ora, {{4}}=motivo
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_RIFIUTATA,
        [nome, data, ora, motivo],
    )


def whatsapp_prenotazione_modificata(prenotazione, vecchia_data: str, vecchia_ora: str) -> bool:
    to = _whatsapp_target(prenotazione)
    if not to:
        return False
    nome = _nome_cliente(prenotazione)
    nuova_data, nuova_ora = _data_ora(prenotazione)
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_MODIFICATA,
        [nome, f'{vecchia_data} {vecchia_ora}', nuova_data, nuova_ora, prenotazione.codice_prenotazione],
    )


def whatsapp_prenotazione_promemoria(prenotazione) -> bool:
    to = _whatsapp_target(prenotazione)
    if not to:
        return False
    nome = _nome_cliente(prenotazione)
    _data, ora = _data_ora(prenotazione)
    # Template approvato: 2 variabili {{1}}=nome, {{2}}=ora
    # (data hardcoded come "OGGI", codice prenotazione rimosso)
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_PROMEMORIA,
        [nome, ora],
    )


# ===========================================================================
# Nuova notifica: auto pronta (ordine completato, pronto al ritiro)
# ===========================================================================

def whatsapp_auto_pronta(ordine) -> bool:
    """Notifica al cliente che la sua auto e' pronta per il ritiro.

    Triggerata quando un ordine passa allo stato 'completato' lato
    operatore. Template approvato: 1 variabile {{1}}=nome.
    """
    cliente = getattr(ordine, 'cliente', None)
    if not cliente:
        return False
    raw_phone = getattr(cliente, 'telefono', '') or ''
    to = _to_e164(raw_phone)
    if not to:
        return False
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    return _send_template(
        to,
        settings.META_WA_TEMPLATE_AUTO_PRONTA,
        [nome],
    )
