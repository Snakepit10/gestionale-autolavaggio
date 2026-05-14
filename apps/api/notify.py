"""Helper sicuro per inviare notifiche WebSocket.

Strategia fire-and-forget in daemon thread: la send Redis avviene in
un thread separato; la view ritorna IMMEDIATAMENTE senza aspettarla.
Se Redis e' lento o non risponde:
- la richiesta HTTP non viene bloccata
- il thread eventualmente termina al timeout interno della libreria
- come daemon, viene killato al shutdown del processo senza warning

Questo elimina sia il blocco runtime (cascade 499/502) sia il warning
'took too long to shut down and was killed' di Daphne.
"""
import logging
import threading

logger = logging.getLogger(__name__)


def _send_in_background(group_name: str, message: dict) -> None:
    """Esegue la send in un thread isolato (con event loop proprio)."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(group_name, message)
    except Exception as e:
        logger.warning(
            'WS notify bg fallito per group=%s type=%s: %s',
            group_name, message.get('type'), e,
        )


def notify_group(group_name: str, message: dict, timeout: float = 2.0) -> bool:
    """Invia un messaggio a un gruppo channels in modo fire-and-forget.

    Ritorna sempre True (la send e' delegata al thread di background).
    NON aspetta il completamento: la view torna entro pochi ms anche
    se Redis e' down. Eventuali errori sono solo loggati nel thread.

    Il parametro `timeout` resta per retrocompatibilita ma non viene
    piu usato (era un parziale workaround che falliva quando le
    coroutine Redis non rispondevano al cancel).
    """
    try:
        thread = threading.Thread(
            target=_send_in_background,
            args=(group_name, message),
            daemon=True,
        )
        thread.start()
        return True
    except Exception as e:
        logger.warning('WS notify thread start fallito: %s', e)
        return False
