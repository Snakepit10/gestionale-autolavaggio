"""Verifica della connessione al broker MQTT.

    python manage.py mqtt_check

Esegue un test completo di andata e ritorno: si connette col
credenziale 'crm', si sottoscrive a un topic di selftest, ci pubblica
un messaggio e verifica di riceverlo. Exit code 0 = tutto ok.
"""
import json
import threading
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand

import paho.mqtt.client as mqtt

from apps.impianto.mqtt import mqtt_configurato


class Command(BaseCommand):
    help = 'Verifica connessione, autenticazione e round-trip col broker MQTT.'

    def handle(self, *args, **options):
        if not mqtt_configurato():
            self.stderr.write(self.style.ERROR(
                'MQTT non configurato: imposta MQTT_HOST, MQTT_PORT, '
                'MQTT_USER, MQTT_PASSWORD.'))
            raise SystemExit(1)

        topic = f'autolavaggio/crm/selftest/{uuid.uuid4().hex[:8]}'
        atteso = json.dumps({'check': 'ok'})
        ricevuto = threading.Event()
        connesso = threading.Event()
        errore = {}

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f'crm-check-{uuid.uuid4().hex[:6]}')
        client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)

        def on_connect(cl, userdata, flags, reason_code, properties):
            if reason_code == 0:
                connesso.set()
                cl.subscribe(topic, qos=1)
            else:
                errore['connessione'] = str(reason_code)

        def on_subscribe(cl, userdata, mid, reason_codes, properties):
            # Sottoscrizione confermata: pubblica il messaggio di test
            cl.publish(topic, atteso, qos=1)

        def on_message(cl, userdata, msg):
            if msg.payload.decode('utf-8') == atteso:
                ricevuto.set()

        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message

        self.stdout.write(
            f'Connessione a {settings.MQTT_HOST}:{settings.MQTT_PORT} '
            f'come "{settings.MQTT_USER}"...')
        try:
            client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=15)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Connessione fallita: {exc}'))
            raise SystemExit(1)

        client.loop_start()
        try:
            if not connesso.wait(timeout=10):
                motivo = errore.get('connessione', 'timeout')
                self.stderr.write(self.style.ERROR(
                    f'Autenticazione/connessione rifiutata: {motivo}'))
                raise SystemExit(1)
            self.stdout.write(self.style.SUCCESS('Connesso e autenticato.'))

            if not ricevuto.wait(timeout=10):
                self.stderr.write(self.style.ERROR(
                    'Round-trip fallito: messaggio di selftest non ricevuto '
                    '(controlla le ACL o i log del broker).'))
                raise SystemExit(1)
            self.stdout.write(self.style.SUCCESS(
                f'Round-trip ok su {topic}: publish e subscribe funzionano.'))
        finally:
            client.loop_stop()
            client.disconnect()
