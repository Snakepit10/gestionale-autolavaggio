from django.apps import AppConfig


class CqConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.cq'
    verbose_name = 'Controllo Qualità'

    def ready(self):
        import apps.cq.signals  # noqa
