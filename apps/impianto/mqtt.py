"""Client MQTT del CRM verso il broker Mosquitto (impianto IoT).

Due ruoli distinti:

1. LISTENER (`crea_listener` + comando `mqtt_listener`): si sottoscrive
   a `autolavaggio/+/events/rpc`, estrae dal payload RPC Shelly gli
   aggiornamenti del contatore impulsi e li salva in EventoImpianto.
   Va eseguito come processo dedicato (`python manage.py mqtt_listener`).

2. PUBLISHER (`moneta_virtuale`): apre una connessione usa-e-getta,
   pubblica il comando RPC `Switch.Set` su `autolavaggio/<nodo>/rpc` e
   chiude. Su OUT1 dello Shelly e' attivo un auto-off hardware di 1 s:
   basta accendere, si spegne da solo -> NON inviamo mai lo spegnimento.

Configurazione da variabili d'ambiente (vedi config/settings.py):
MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD. La riconnessione del
listener e' automatica con backoff esponenziale (1s -> 120s).
"""
import json
import logging
import time
import uuid

from django.conf import settings
from django.db import close_old_connections

import paho.mqtt.client as mqtt

logger = logging.getLogger('apps.impianto.mqtt')

# Topic dove i nodi pubblicano le notifiche RPC (Shelly Gen2+):
# autolavaggio/<nodo>/events/rpc
TOPIC_EVENTI = 'autolavaggio/+/events/rpc'


def mqtt_configurato() -> bool:
    """True se le variabili d'ambiente minime sono presenti."""
    return bool(settings.MQTT_HOST and settings.MQTT_USER)


def _nuovo_client(client_id: str) -> mqtt.Client:
    """Client paho-mqtt gia' configurato con credenziali e backoff."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
    )
    client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)
    # Riconnessione automatica con backoff esponenziale
    client.reconnect_delay_set(min_delay=1, max_delay=120)
    # I tentativi di connessione falliti (DNS, refused, timeout) finiscono
    # nel logger: senza questo il listener ritenterebbe in silenzio
    client.enable_logger(logger)
    return client


# ---------------------------------------------------------------------
# Parsing payload Shelly
# ---------------------------------------------------------------------

def estrai_eventi(payload: dict) -> list:
    """Estrae eventi normalizzati da una notifica RPC Shelly.

    Ritorna una lista di tuple (tipo_evento, valore):
    - NotifyStatus con "input:N" contenente counts.total -> l'update del
      contatore impulsi (COUNT IN, sullo Shelly pista2 e' l'input id 2):
      ('contatore', totale)
    - NotifyEvent -> un evento per elemento di params.events:
      ('input:2:single_push', None) e simili.
    Payload non riconosciuti -> lista vuota (il chiamante decide se
    salvarli comunque come 'raw').
    """
    eventi = []
    metodo = payload.get('method') or ''
    params = payload.get('params') or {}

    if metodo == 'NotifyStatus':
        for chiave, stato in params.items():
            if not (chiave.startswith('input:') and isinstance(stato, dict)):
                continue
            counts = stato.get('counts') or {}
            if 'total' in counts:
                try:
                    eventi.append(('contatore', int(counts['total'])))
                except (TypeError, ValueError):
                    logger.warning('counts.total non numerico: %r', counts)
    elif metodo == 'NotifyEvent':
        for ev in (params.get('events') or []):
            componente = ev.get('component', '?')
            nome = ev.get('event', '?')
            eventi.append((f'{componente}:{nome}', None))

    return eventi


def _gestisci_messaggio(client, userdata, msg):
    """Callback on_message del listener: parse + salvataggio evento."""
    from .models import EventoImpianto

    # Il topic e' autolavaggio/<nodo>/events/rpc -> nodo in posizione 1
    parti = msg.topic.split('/')
    nodo = parti[1] if len(parti) > 1 else '?'

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        logger.warning('[%s] payload non JSON su %s: %r',
                       nodo, msg.topic, msg.payload[:200])
        return

    eventi = estrai_eventi(payload)
    if not eventi:
        logger.debug('[%s] notifica senza eventi riconosciuti (method=%s)',
                     nodo, payload.get('method'))
        return

    # Il callback gira nel thread di rete paho: chiudi eventuali
    # connessioni DB stantie prima di scrivere (pattern Django safe
    # fuori dal ciclo request/response).
    close_old_connections()
    for tipo_evento, valore in eventi:
        # Lo Shelly ripubblica il contatore ogni minuto anche a valore
        # invariato: salviamo solo i CAMBI, altrimenti ~1.400 righe
        # identiche al giorno per nodo.
        if tipo_evento == 'contatore':
            ultimo = (EventoImpianto.objects
                      .filter(nodo=nodo, tipo_evento='contatore')
                      .order_by('-pk')
                      .values_list('valore', flat=True)
                      .first())
            if ultimo == valore:
                continue
        EventoImpianto.objects.create(
            nodo=nodo,
            tipo_evento=tipo_evento,
            valore=valore,
            payload=payload,
        )
        logger.info('[%s] %s%s', nodo, tipo_evento,
                    f' = {valore}' if valore is not None else '')


def crea_listener() -> mqtt.Client:
    """Costruisce il client listener (senza avviare il loop).

    Il chiamante (management command mqtt_listener) fa connect +
    loop_forever: paho gestisce da solo le riconnessioni col backoff
    impostato in _nuovo_client.
    """
    client = _nuovo_client('crm')

    def on_connect(cl, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info('Connesso al broker %s:%s, sottoscrivo %s',
                        settings.MQTT_HOST, settings.MQTT_PORT, TOPIC_EVENTI)
            # (Ri)sottoscrizione qui: vale anche dopo ogni riconnessione
            cl.subscribe(TOPIC_EVENTI, qos=1)
        else:
            logger.error('Connessione rifiutata dal broker: %s', reason_code)

    def on_disconnect(cl, userdata, flags, reason_code, properties):
        logger.warning('Disconnesso dal broker (%s): riconnessione '
                       'automatica con backoff...', reason_code)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = _gestisci_messaggio
    return client


# ---------------------------------------------------------------------
# Comandi verso i nodi
# ---------------------------------------------------------------------

def moneta_virtuale(nodo: str, impulsi: int = 1, switch_id: int = 1) -> tuple:
    """Simula l'inserimento di monete sul nodo indicato.

    Pubblica su autolavaggio/<nodo>/rpc il comando RPC Shelly
    Switch.Set(on=true) sull'uscita `switch_id`. Sulla pista2 il rele'
    della gettoniera e' OUT2 (Switch id 1) con auto-off hardware di
    1 s: NON inviamo alcun comando di spegnimento. Per piu' impulsi il
    comando viene ripetuto con 1 s di pausa (l'auto-off ha gia'
    riaperto il rele').

    Ritorna (ok, messaggio, inviati): `inviati` e' il numero di impulsi
    effettivamente confermati dal broker, indispensabile al modulo
    monete per stornare gli impulsi non partiti in caso di errore a
    meta' sequenza.
    """
    if not mqtt_configurato():
        return False, 'MQTT non configurato (MQTT_HOST/MQTT_USER mancanti).', 0
    if impulsi < 1:
        return False, 'Il numero di impulsi deve essere >= 1.', 0

    topic = f'autolavaggio/{nodo}/rpc'
    payload = json.dumps({
        'id': 1,
        'src': 'crm',
        'method': 'Switch.Set',
        'params': {'id': switch_id, 'on': True},
    })

    # Connessione usa-e-getta: funziona da qualunque processo (web,
    # shell) senza dipendere dal listener. client_id univoco per non
    # scalzare la sessione del listener che usa l'utente 'crm'.
    client = _nuovo_client(f'crm-pub-{uuid.uuid4().hex[:8]}')
    inviati = 0
    try:
        client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        client.loop_start()
        for i in range(impulsi):
            info = client.publish(topic, payload, qos=1)
            info.wait_for_publish(timeout=10)
            if not info.is_published():
                return False, (f'Impulso {i + 1}/{impulsi} NON confermato dal '
                               f'broker (timeout).'), inviati
            inviati += 1
            logger.info('moneta_virtuale: impulso %s/%s inviato a %s',
                        i + 1, impulsi, topic)
            if i < impulsi - 1:
                time.sleep(1)  # lascia all'auto-off il tempo di riaprire
        return True, f'{impulsi} impulso/i inviato/i a {topic}.', inviati
    except Exception as exc:  # rete giu', DNS, auth: riporta l'errore
        logger.error('moneta_virtuale fallita: %s', exc)
        return False, f'Errore di pubblicazione: {exc}', inviati
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass
