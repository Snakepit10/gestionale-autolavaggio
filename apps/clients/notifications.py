"""Notifiche email cliente per le prenotazioni.

Wrapper sopra django.core.mail.send_mail con template testuali. In dev
usa console backend (le email appaiono in stdout); in produzione SMTP
configurato via env vars.

Phase 2 (futuro): SMS via Twilio, WhatsApp via Twilio/Meta Cloud API.
"""
import logging
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


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
        f"Ti aspettiamo. Per modifiche o annullamenti contattaci."
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
