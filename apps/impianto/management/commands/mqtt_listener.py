"""Listener MQTT dell'impianto: da eseguire come servizio dedicato.

Su Railway: nuovo servizio dallo stesso repo del CRM con start command
    python manage.py mqtt_listener
e le stesse variabili d'ambiente (DATABASE_URL, MQTT_*).

Si sottoscrive a autolavaggio/+/events/rpc e salva gli eventi
(contatore impulsi, eventi input) in EventoImpianto. La riconnessione
e' automatica con backoff (gestita da paho); se anche la PRIMA
connessione fallisce, ritenta da solo.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.impianto.mqtt import crea_listener, mqtt_configurato


class Command(BaseCommand):
    help = 'Avvia il listener MQTT degli eventi impianto (processo dedicato).'

    def handle(self, *args, **options):
        # I log del modulo mqtt devono arrivare a stdout (log Railway)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(name)s %(message)s',
        )

        if not mqtt_configurato():
            self.stderr.write(self.style.ERROR(
                'MQTT non configurato: imposta MQTT_HOST, MQTT_PORT, '
                'MQTT_USER, MQTT_PASSWORD.'))
            raise SystemExit(1)

        self.stdout.write(
            f'Listener MQTT verso {settings.MQTT_HOST}:{settings.MQTT_PORT} '
            f'(utente {settings.MQTT_USER})...')

        client = crea_listener()
        # connect_async + loop_forever: se il broker non e' raggiungibile
        # alla partenza, paho ritenta con lo stesso backoff delle
        # riconnessioni invece di crashare il servizio.
        client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT,
                             keepalive=60)
        try:
            client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            self.stdout.write('Arresto listener...')
            client.disconnect()
