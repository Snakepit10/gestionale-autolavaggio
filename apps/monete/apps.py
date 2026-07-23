from django.apps import AppConfig


class MoneteConfig(AppConfig):
    """Monete virtuali: portafoglio clienti, pacchetti acquistabili
    online (Stripe/PayPal), ricariche da operatore e avvio dei nodi
    dell'impianto (via apps.impianto) a scalare dal saldo.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.monete'
    verbose_name = 'Monete virtuali'
