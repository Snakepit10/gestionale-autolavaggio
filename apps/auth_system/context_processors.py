from django.conf import settings


def google_oauth(request):
    """Espone ai template se il login Google e' configurato (env vars),
    cosi' i bottoni 'Continua con Google' compaiono solo quando
    possono funzionare."""
    return {'google_oauth_enabled': settings.GOOGLE_OAUTH_ENABLED}
