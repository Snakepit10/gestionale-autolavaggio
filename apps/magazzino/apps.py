from django.apps import AppConfig


class MagazzinoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.magazzino'
    verbose_name = 'Magazzino'

    def ready(self):
        from . import signals  # noqa: F401
