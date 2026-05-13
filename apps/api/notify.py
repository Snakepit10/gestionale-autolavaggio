"""Helper sicuro per inviare notifiche WebSocket.

Problema risolto: chiamate dirette a `async_to_sync(channel_layer.group_send)`
in view sync possono bloccare indefinitamente se Redis e' irraggiungibile o
lento, mandando il worker Daphne in deadlock. Risultato: 499/502 a cascata.

Soluzione: timeout duro (default 2s) tramite asyncio.wait_for. Se la send
non termina nel timeout, si scarta silenziosamente senza bloccare la
risposta HTTP. Errori (incluso TimeoutError) sono solo loggati.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def notify_group(group_name: str, message: dict, timeout: float = 2.0) -> bool:
    """Invia un messaggio a un gruppo channels senza bloccare la view.

    Ritorna True se inviato, False altrimenti (errore o timeout). Non
    solleva eccezioni: in caso di problema logga warning e prosegue.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return False

        async def _send_with_timeout():
            await asyncio.wait_for(
                channel_layer.group_send(group_name, message),
                timeout=timeout,
            )

        async_to_sync(_send_with_timeout)()
        return True
    except asyncio.TimeoutError:
        logger.warning(
            'WS notify timeout (%.1fs) per group=%s type=%s',
            timeout, group_name, message.get('type'),
        )
        return False
    except Exception as e:
        logger.warning(
            'WS notify fallito per group=%s type=%s: %s',
            group_name, message.get('type'), e,
        )
        return False
