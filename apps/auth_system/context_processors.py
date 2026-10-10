from django.conf import settings


def google_oauth(request):
    """Espone ai template se il login Google e' configurato (env vars),
    cosi' i bottoni 'Continua con Google' compaiono solo quando
    possono funzionare."""
    return {'google_oauth_enabled': settings.GOOGLE_OAUTH_ENABLED}


def sezioni_visibili(request):
    """Sezioni del gestionale accessibili all'utente, per il menu."""
    from .sezioni import sezioni_permesse
    visibili = sezioni_permesse(getattr(request, 'user', None))
    contesto = {'sezioni_visibili': visibili}
    if 'magazzino' in visibili:
        from django.utils.functional import SimpleLazyObject

        def conta():
            from apps.magazzino.models import Assegnazione
            return Assegnazione.objects.filter(da_gestire=True).count()
        contesto['n_segnalazioni_magazzino'] = SimpleLazyObject(conta)
    return contesto
