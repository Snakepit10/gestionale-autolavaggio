"""Notifiche cliente per le prenotazioni.

Modulo che combina i due canali:
- WhatsApp Cloud API (Meta) come canale primario (vedi
  `apps/clients/whatsapp.py`)
- Email come fallback automatico quando WhatsApp non e' configurato
  o l'invio fallisce

Il chiamante (view) usa solo le funzioni `notifica_prenotazione_*`
e non sa quale canale parte: la scelta e' centralizzata qui.

In dev l'email usa console backend (stdout); in produzione SMTP via
env vars.
"""
import logging
from django.conf import settings
from django.core.mail import send_mail

from . import whatsapp as wa

logger = logging.getLogger(__name__)


# Condizioni di prenotazione mostrate al cliente nel modal di conferma
# e accodate alle email di ricevuta/conferma. Mantenere coerente con il
# testo del modale in templates/clients/booking.html.
CONDIZIONI_PRENOTAZIONE = (
    "\n\n— CONDIZIONI DI PRENOTAZIONE —\n"
    "La prenotazione inizia all'orario selezionato e ha una durata\n"
    "stimata in base ai servizi scelti.\n\n"
    "Ti chiediamo di segnalarci telefonicamente eventuali ritardi o\n"
    "necessita' di modifica. In caso di mancato avviso, dopo 15 minuti\n"
    "di ritardo la slot potrebbe essere ceduta ad altri clienti in\n"
    "attesa.\n\n"
    "Per annullare o modificare la prenotazione contattaci\n"
    "telefonicamente al 379 233 7051.\n\n"
    "Il prezzo finale dei servizi viene comunicato al ritiro dell'auto,\n"
    "in base ai servizi effettivamente erogati.\n"
)


def _safe_send(subject: str, body: str, to_email: str | None) -> bool:
    if not to_email:
        logger.warning('Email skipped: destinatario vuoto (subject=%s)', subject)
        return False
    try:
        sent = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [to_email],
            fail_silently=False,  # log full error instead of swallow
        )
        if sent:
            logger.info('Email inviata a %s: %s', to_email, subject)
            return True
        logger.warning('send_mail ha ritornato 0 per %s (%s)', to_email, subject)
        return False
    except Exception as e:
        logger.error('Errore invio email a %s (subject=%s): %s', to_email, subject, e, exc_info=True)
        return False


def _email_target(prenotazione, override: str | None = None) -> str:
    """Determina email destinazione per le notifiche di una prenotazione.

    Priorita: override esplicito > email_contatto salvato sulla Prenotazione
    > cliente.email > user.email.
    """
    if override:
        return override
    if getattr(prenotazione, 'email_contatto', '') :
        return prenotazione.email_contatto
    cliente = prenotazione.cliente
    if cliente and cliente.email:
        return cliente.email
    if cliente and cliente.user_id and cliente.user.email:
        return cliente.user.email
    return ''


def email_prenotazione_ricevuta(prenotazione, to_email: str | None = None) -> bool:
    cliente = prenotazione.cliente
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    data = prenotazione.slot.data.strftime('%d/%m/%Y')
    ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    servizi = ', '.join(s.titolo for s in prenotazione.servizi.all())
    body = (
        f"Ciao {nome},\n\n"
        f"abbiamo ricevuto la tua richiesta di prenotazione:\n"
        f"- Data: {data}\n"
        f"- Ora: {ora}\n"
        f"- Servizi: {servizi}\n"
        f"- Codice: {prenotazione.codice_prenotazione}\n\n"
        f"L'autolavaggio confermera a breve la disponibilita. "
        f"Riceverai una nuova email all'esito.\n\n"
        f"Grazie!"
        f"{CONDIZIONI_PRENOTAZIONE}"
    )
    return _safe_send(
        'Prenotazione ricevuta - in attesa di conferma',
        body,
        _email_target(prenotazione, override=to_email),
    )


def email_prenotazione_confermata(prenotazione) -> bool:
    cliente = prenotazione.cliente
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    data = prenotazione.slot.data.strftime('%d/%m/%Y')
    ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    body = (
        f"Ciao {nome},\n\n"
        f"la tua prenotazione e' stata CONFERMATA!\n\n"
        f"- Data: {data}\n"
        f"- Ora: {ora}\n"
        f"- Codice: {prenotazione.codice_prenotazione}\n\n"
        f"Ti aspettiamo."
        f"{CONDIZIONI_PRENOTAZIONE}"
    )
    return _safe_send('Prenotazione confermata', body, _email_target(prenotazione))


def email_prenotazione_rifiutata(prenotazione, motivo: str = '') -> bool:
    cliente = prenotazione.cliente
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    data = prenotazione.slot.data.strftime('%d/%m/%Y')
    ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    body = (
        f"Ciao {nome},\n\n"
        f"purtroppo non possiamo confermare la tua prenotazione del "
        f"{data} alle {ora}.\n\n"
    )
    if motivo:
        body += f"Motivo: {motivo}\n\n"
    body += (
        f"Codice prenotazione: {prenotazione.codice_prenotazione}\n\n"
        f"Ti invitiamo a riprovare scegliendo un'altra fascia oraria sul nostro sito.\n"
        f"Ci scusiamo per il disagio."
    )
    return _safe_send('Prenotazione non confermata', body, _email_target(prenotazione))


def email_prenotazione_modificata(prenotazione, vecchia_data: str, vecchia_ora: str) -> bool:
    """Operatore propone una nuova fascia oraria modificando lo slot."""
    cliente = prenotazione.cliente
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    nuova_data = prenotazione.slot.data.strftime('%d/%m/%Y')
    nuova_ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    body = (
        f"Ciao {nome},\n\n"
        f"la tua prenotazione del {vecchia_data} alle {vecchia_ora} e' stata "
        f"riprogrammata.\n\n"
        f"Nuova data: {nuova_data}\n"
        f"Nuova ora: {nuova_ora}\n"
        f"Codice: {prenotazione.codice_prenotazione}\n\n"
        f"Se la nuova fascia non ti va bene, contattaci o annulla la "
        f"prenotazione dalla tua area cliente."
    )
    return _safe_send('Prenotazione riprogrammata', body, _email_target(prenotazione))


def email_prenotazione_promemoria(prenotazione) -> bool:
    """Promemoria pre-appuntamento (~1h prima dello slot)."""
    cliente = prenotazione.cliente
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    data = prenotazione.slot.data.strftime('%d/%m/%Y')
    ora = prenotazione.slot.ora_inizio.strftime('%H:%M')
    body = (
        f"Ciao {nome},\n\n"
        f"ti ricordiamo la prenotazione di OGGI alle {ora}.\n"
        f"Codice: {prenotazione.codice_prenotazione}\n\n"
        f"Indirizzo: Via Palma 302, Licata (AG)\n"
        f"In caso di ritardo chiamaci al 379 233 7051.\n\n"
        f"A tra poco!"
    )
    return _safe_send('Promemoria prenotazione', body, _email_target(prenotazione))


# ===========================================================================
# Wrapper unificati: WhatsApp primary, email fallback.
# Usate dalle view al posto dei `email_*` diretti.
# ===========================================================================

def notifica_prenotazione_ricevuta(prenotazione, to_email: str | None = None) -> bool:
    if wa.whatsapp_prenotazione_ricevuta(prenotazione):
        return True
    return email_prenotazione_ricevuta(prenotazione, to_email=to_email)


def notifica_prenotazione_confermata(prenotazione) -> bool:
    if wa.whatsapp_prenotazione_confermata(prenotazione):
        return True
    return email_prenotazione_confermata(prenotazione)


def notifica_prenotazione_rifiutata(prenotazione, motivo: str = '') -> bool:
    if wa.whatsapp_prenotazione_rifiutata(prenotazione, motivo):
        return True
    return email_prenotazione_rifiutata(prenotazione, motivo)


def notifica_prenotazione_modificata(prenotazione, vecchia_data: str, vecchia_ora: str) -> bool:
    if wa.whatsapp_prenotazione_modificata(prenotazione, vecchia_data, vecchia_ora):
        return True
    return email_prenotazione_modificata(prenotazione, vecchia_data, vecchia_ora)


def notifica_prenotazione_promemoria(prenotazione) -> bool:
    if wa.whatsapp_prenotazione_promemoria(prenotazione):
        return True
    return email_prenotazione_promemoria(prenotazione)


def email_auto_pronta(ordine) -> bool:
    """Email fallback: auto pronta al ritiro."""
    cliente = getattr(ordine, 'cliente', None)
    if not cliente:
        return False
    nome = (cliente.nome or cliente.cognome or '').strip() or 'Cliente'
    body = (
        f"Ciao {nome},\n\n"
        f"la tua auto e' pronta per il ritiro.\n\n"
        f"Puoi ritirarla negli orari di apertura: dalle 8:00 alle 13:00\n"
        f"e dalle 15:00 alle 19:00.\n\n"
        f"Ti chiediamo di rispettare questi orari: oltre l'orario indicato\n"
        f"non siamo tenuti ad attendere il ritiro.\n\n"
        f"Indirizzo: Via Palma 302, Licata (AG)\n"
        f"Tel. 379 233 7051"
    )
    to_email = cliente.email or ''
    return _safe_send('La tua auto e\' pronta', body, to_email)


def notifica_auto_pronta(ordine) -> bool:
    """Notifica al cliente che la sua auto e' pronta. WhatsApp primary,
    email fallback. Chiamala quando un ordine passa allo stato
    'completato' (vedi apps/ordini/views.py)."""
    if wa.whatsapp_auto_pronta(ordine):
        return True
    return email_auto_pronta(ordine)
