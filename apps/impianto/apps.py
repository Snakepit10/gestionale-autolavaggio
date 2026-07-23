from django.apps import AppConfig


class ImpiantoConfig(AppConfig):
    """Modulo IoT dell'impianto: eventi dai dispositivi in pista
    (Shelly, Waveshare) via MQTT e comandi verso di essi.

    Il listener MQTT NON parte col web process: gira come servizio
    dedicato (`python manage.py mqtt_listener`), cosi' i cron e i
    worker non aprono connessioni duplicate al broker.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.impianto'
    verbose_name = 'Impianto IoT'
