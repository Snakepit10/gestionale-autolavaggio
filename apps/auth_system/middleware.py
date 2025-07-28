from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin


class AuthenticationMiddleware(MiddlewareMixin):
    """
    Middleware per gestire l'autenticazione e i redirect automatici
    """
    
    # URL che richiedono autenticazione operatori (staff)
    OPERATOR_REQUIRED_PATHS = [
        '/ordini/',
        '/postazioni/',
        '/clienti/admin/',
        '/abbonamenti/configurazioni/',
        '/api/',
        '/scorte/',
        '/stampanti/',
        '/sconti/',
        '/categorie/',
        '/catalogo/',
    ]
    
    # URL che richiedono autenticazione clienti
    CLIENT_REQUIRED_PATHS = [
        '/clienti/area-cliente/',
        '/prenotazioni/prenota/',
        '/abbonamenti/shop/',
    ]
    
    # URL pubblici (non richiedono autenticazione)
    PUBLIC_PATHS = [
        '/auth/',
        '/admin/',
        '/',
        '/abbonamenti/verifica/',
        '/api/servizi/',
    ]
    
    def process_request(self, request):
        path = request.path
        user = request.user
        
        # Skip per URL pubblici
        if any(path.startswith(public_path) for public_path in self.PUBLIC_PATHS):
            return None
        
        # Skip per utenti non autenticati sulle pagine pubbliche
        if not user.is_authenticated:
            # Verifica se sta tentando di accedere a URL protetti
            if any(path.startswith(protected_path) for protected_path in self.OPERATOR_REQUIRED_PATHS + self.CLIENT_REQUIRED_PATHS):
                # Determina il tipo di login necessario
                if any(path.startswith(op_path) for op_path in self.OPERATOR_REQUIRED_PATHS):
                    messages.warning(request, 'Effettua il login per accedere a questa area.')
                    return redirect('auth:operator-login')
                else:
                    messages.warning(request, 'Effettua il login per accedere a questa area.')
                    return redirect('auth:client-login')
            return None
        
        # Verifica autorizzazioni per utenti autenticati
        if any(path.startswith(op_path) for op_path in self.OPERATOR_REQUIRED_PATHS):
            # Richiede permessi staff
            if not user.is_staff:
                messages.error(request, 'Non hai i permessi per accedere a questa area.')
                return redirect('auth:client-dashboard' if hasattr(user, 'cliente') else 'core:home')
        
        elif any(path.startswith(client_path) for client_path in self.CLIENT_REQUIRED_PATHS):
            # Richiede account cliente
            if not hasattr(user, 'cliente'):
                messages.error(request, 'Devi essere un cliente registrato per accedere a questa area.')
                return redirect('auth:client-login')
        
        return None