from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin


class CompletamentoProfiloMiddleware(MiddlewareMixin):
    """Cliente loggato senza telefono (tipicamente registrato con
    Google, che non lo fornisce) -> rimandato al form 'completa
    profilo' quando naviga l'area clienti. Il telefono e' obbligatorio
    per notifiche WhatsApp e anti-duplicati anagrafica."""

    SCOPE_PREFIXES = ('/app/', '/auth/clienti/dashboard/')
    # No redirect su asset PWA e chiamate API (romperebbe fetch/manifest)
    EXCLUDE_SUBSTRINGS = ('/api/', 'manifest', 'sw.js', 'service-worker')

    def process_request(self, request):
        path = request.path
        if not any(path.startswith(p) for p in self.SCOPE_PREFIXES):
            return None
        if any(s in path for s in self.EXCLUDE_SUBSTRINGS):
            return None
        user = request.user
        if not user.is_authenticated:
            return None
        cliente = getattr(user, 'cliente', None)
        if cliente is None or (cliente.telefono or '').strip():
            return None
        return redirect('auth:completa-profilo')


class AuthenticationMiddleware(MiddlewareMixin):
    """Vieta le pagine del gestionale agli utenti non-staff.

    Storia: la versione precedente aveva PUBLIC_PATHS = [..., '/'] con
    match per prefisso: '/' e' prefisso di QUALSIASI path, quindi il
    middleware usciva subito e non proteggeva nulla. La protezione
    reale era solo il login_required delle view, che pero' un CLIENTE
    loggato supera: poteva aprire /ordini/, la cassa, ecc.

    Ora: un utente autenticato senza permessi staff (niente is_staff,
    niente gruppi operativi) che visita un prefisso del gestionale
    viene rimandato alla sua area cliente. Gli anonimi non vengono
    toccati qui (le view protette rimandano gia' al login giusto, e i
    webhook/endpoint pubblici come quello Meta restano raggiungibili).
    """

    # Prefissi del gestionale (staff-only)
    STAFF_PREFIXES = (
        '/ordini/', '/postazioni/', '/finanze/', '/cq/', '/turni/',
        '/cartellini/', '/messaggi/', '/marketing/',
        '/categorie/', '/catalogo/', '/sconti/', '/stampanti/', '/scorte/',
        '/clienti/', '/prenotazioni/', '/abbonamenti/', '/api/', '/monete/',
    )

    # Sotto-percorsi CLIENTE dentro prefissi staff: restano accessibili
    CLIENT_ALLOWED_PREFIXES = (
        '/clienti/area-cliente/',
        '/prenotazioni/prenota/',
        '/abbonamenti/shop/',
        '/abbonamenti/verifica/',
        '/api/servizi/',  # catalogo pubblico (core), usato dalle pagine cliente
    )

    def process_request(self, request):
        user = request.user
        if not user.is_authenticated:
            return None
        if user.is_staff or user.groups.exists():
            return None

        path = request.path
        if any(path.startswith(p) for p in self.CLIENT_ALLOWED_PREFIXES):
            return None
        # '/' esatto = dashboard staff; il resto per prefisso
        if path == '/' or any(path.startswith(p) for p in self.STAFF_PREFIXES):
            messages.error(request, 'Area riservata agli operatori.')
            return redirect('clients:dashboard')
        return None