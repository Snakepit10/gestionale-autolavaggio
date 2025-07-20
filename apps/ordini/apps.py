from django.apps import AppConfig


class OrdiniConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ordini'
    verbose_name = 'Ordini'
    
    def ready(self):
        import apps.ordini.signals