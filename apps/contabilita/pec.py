"""Lettura delle fatture dalla casella PEC (IMAP).

Lo SDI consegna ogni fattura in un messaggio con allegati il file della
fattura (.xml o .xml.p7m) e i metadati (_MT_); la PEC lo incapsula in
postacert.eml. Si leggono solo gli allegati che sono fatture; ricevute,
notifiche e metadati si ignorano. I messaggi gia' letti (Message-ID)
non si rileggono; la casella si apre in sola lettura.

Configurazione con variabili d'ambiente (le imposta il titolare su
Railway): PEC_IMAP_HOST, PEC_IMAP_USER, PEC_IMAP_PASSWORD e, facoltative,
PEC_IMAP_PORT (993) e PEC_CARTELLA (INBOX)."""
import email
import imaplib
import os
from datetime import date, timedelta
from email import policy
from email.utils import parsedate_to_datetime

from django.utils import timezone

from .fatturapa import e_fattura
from .importa import Esito, importa_file
from .models import MessaggioPec

MESI_IMAP = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


class ErrorePec(Exception):
    pass


def configurata():
    return all(os.environ.get(k) for k in ('PEC_IMAP_HOST', 'PEC_IMAP_USER', 'PEC_IMAP_PASSWORD'))


def allegati_fattura(messaggio):
    """(nome, bytes) degli allegati che sono fatture, anche dentro i
    messaggi incapsulati (postacert.eml)."""
    for parte in messaggio.walk():
        nome = parte.get_filename()
        if nome and e_fattura(nome):
            dati = parte.get_payload(decode=True)
            if dati:
                yield nome, dati


def importa_messaggio(grezzo, message_id=None):
    """Importa le fatture di un messaggio e lo segna come letto."""
    messaggio = email.message_from_bytes(grezzo, policy=policy.default)
    message_id = (message_id or str(messaggio.get('Message-ID') or '')).strip()[:255]
    esito = Esito()
    for nome, dati in allegati_fattura(messaggio):
        importa_file(nome, dati, 'pec', esito)
    try:
        ricevuto = parsedate_to_datetime(str(messaggio['Date'])) if messaggio['Date'] else None
    except (TypeError, ValueError):
        ricevuto = None
    testo = esito.testo() if (esito.nuove or esito.doppie or esito.errori) else 'nessuna fattura'
    if esito.errori:
        testo += ': ' + '; '.join(esito.errori)
    MessaggioPec.objects.update_or_create(message_id=message_id, defaults={
        'oggetto': str(messaggio.get('Subject') or '')[:255], 'ricevuto_il': ricevuto,
        'fatture': len(esito.nuove), 'esito': testo[:255]})
    return esito


def leggi_casella(giorni=30):
    """Legge i messaggi degli ultimi `giorni` non ancora letti."""
    if not configurata():
        raise ErrorePec('PEC non configurata (variabili PEC_IMAP_HOST, PEC_IMAP_USER, PEC_IMAP_PASSWORD)')
    cartella = os.environ.get('PEC_CARTELLA', 'INBOX')
    totale = Esito()
    letti = 0
    try:
        imap = imaplib.IMAP4_SSL(os.environ['PEC_IMAP_HOST'], int(os.environ.get('PEC_IMAP_PORT', 993)),
                                 timeout=60)
    except OSError as e:
        raise ErrorePec(f'server PEC non raggiungibile ({e})')
    try:
        imap.login(os.environ['PEC_IMAP_USER'], os.environ['PEC_IMAP_PASSWORD'])
        stato, _ = imap.select(f'"{cartella}"', readonly=True)
        if stato != 'OK':
            raise ErrorePec(f'cartella "{cartella}" non trovata')
        dal = date.today() - timedelta(days=giorni)
        _, trovati = imap.uid('search', None, 'SINCE', f'{dal.day:02d}-{MESI_IMAP[dal.month - 1]}-{dal.year}')
        for uid in (trovati[0] or b'').split():
            _, intestazione = imap.uid('fetch', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
            testa = intestazione[0][1] if intestazione and isinstance(intestazione[0], tuple) else b''
            message_id = str(email.message_from_bytes(testa).get('Message-ID') or '').strip()
            message_id = message_id or f'uid:{cartella}:{uid.decode()}'
            if MessaggioPec.objects.filter(message_id=message_id[:255]).exists():
                continue
            _, corpo = imap.uid('fetch', uid, '(BODY.PEEK[])')
            if not corpo or not isinstance(corpo[0], tuple):
                continue
            esito = importa_messaggio(corpo[0][1], message_id)
            letti += 1
            totale.nuove += esito.nuove
            totale.doppie += esito.doppie
            totale.errori += esito.errori
    except imaplib.IMAP4.error as e:
        raise ErrorePec(f'accesso alla PEC non riuscito ({e})')
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    return letti, totale


def ultima_lettura():
    ultimo = MessaggioPec.objects.order_by('-letto_il').first()
    return timezone.localtime(ultimo.letto_il) if ultimo else None
