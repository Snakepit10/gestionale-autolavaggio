from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from apps.clienti.models import Cliente
from apps.prenotazioni.models import Prenotazione
from apps.abbonamenti.models import Abbonamento
from django.db.models import Q
from datetime import datetime, timedelta


def operator_login(request):
    """Login per operatori/staff"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        remember_me = request.POST.get('remember_me')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None and user.is_staff:
            login(request, user)
            
            # Gestisci "ricordami"
            if not remember_me:
                request.session.set_expiry(0)  # Scade alla chiusura del browser
            else:
                request.session.set_expiry(1209600)  # 2 settimane
            
            messages.success(request, f'Benvenuto, {user.get_full_name() or user.username}!')
            
            # Redirect alla dashboard operatori
            next_url = request.GET.get('next', 'core:home')
            return redirect(next_url)
        else:
            messages.error(request, 'Credenziali non valide o accesso non autorizzato.')
    
    return render(request, 'auth/operator_login.html')


def client_login(request):
    """Login per clienti"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        remember_me = request.POST.get('remember_me')
        
        # Cerca l'utente per email
        try:
            cliente = Cliente.objects.get(email=email)
            if cliente.user:
                user = authenticate(request, username=cliente.user.username, password=password)
                
                if user is not None:
                    login(request, user)
                    
                    # Gestisci "ricordami"
                    if remember_me:
                        request.session.set_expiry(2592000)  # 30 giorni
                    else:
                        request.session.set_expiry(0)  # Scade alla chiusura del browser
                    
                    messages.success(request, f'Benvenuto, {cliente.nome_completo}!')
                    return redirect('clients:dashboard')
                else:
                    messages.error(request, 'Password non corretta.')
            else:
                messages.error(request, 'Account non ancora attivato. Contatta il servizio clienti.')
        except Cliente.DoesNotExist:
            messages.error(request, 'Email non registrata nel sistema.')
    
    return render(request, 'auth/client_login.html')


def operator_logout(request):
    """Logout operatori"""
    user_name = request.user.get_full_name() or request.user.username if request.user.is_authenticated else None
    logout(request)
    
    if user_name:
        messages.success(request, f'Arrivederci, {user_name}! Sessione terminata con successo.')
    
    return redirect('auth:operator-login')


def client_logout(request):
    """Logout clienti"""
    if request.user.is_authenticated:
        try:
            cliente = Cliente.objects.get(user=request.user)
            cliente_nome = cliente.nome_completo
        except Cliente.DoesNotExist:
            cliente_nome = request.user.get_full_name() or request.user.username
        
        logout(request)
        messages.success(request, f'Arrivederci, {cliente_nome}! A presto!')
    
    return redirect('auth:client-login')


class ClientDashboardView(LoginRequiredMixin, TemplateView):
    """Dashboard clienti"""
    template_name = 'auth/client_landing.html'
    login_url = reverse_lazy('auth:client-login')
    
    def dispatch(self, request, *args, **kwargs):
        # Verifica che l'utente sia un cliente
        if not hasattr(request.user, 'cliente'):
            messages.error(request, 'Accesso non autorizzato.')
            return redirect('auth:client-login')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        try:
            cliente = Cliente.objects.get(user=user)
            
            # Prenotazioni prossime
            prenotazioni_prossime = Prenotazione.objects.filter(
                cliente=cliente,
                data_ora__gte=datetime.now(),
                stato__in=['confermata', 'check_in']
            ).order_by('data_ora')[:5]
            
            # Abbonamenti attivi
            abbonamenti = Abbonamento.objects.filter(
                cliente=cliente
            ).order_by('-data_inizio')[:3]
            
            # Statistiche
            context.update({
                'cliente': cliente,
                'prenotazioni_prossime': prenotazioni_prossime,
                'prenotazioni_attive': prenotazioni_prossime.count(),
                'abbonamenti': abbonamenti,
                'abbonamenti_attivi': abbonamenti.filter(
                    data_scadenza__gte=datetime.now().date()
                ).count(),
                'punti_fedelta': cliente.punti_fedelta.punti_disponibili if hasattr(cliente, 'punti_fedelta') else 0,
                'servizi_totali': Prenotazione.objects.filter(
                    cliente=cliente,
                    stato='completata'
                ).count(),
            })
            
            # Ultimi movimenti punti
            if hasattr(cliente, 'punti_fedelta'):
                context['ultimi_movimenti_punti'] = cliente.movimenti_punti.all()[:3]
            
            # Attività recente (simulata per ora)
            attivita_recente = []
            
            # Aggiungi prenotazioni recenti
            for prenotazione in prenotazioni_prossime[:2]:
                attivita_recente.append({
                    'descrizione': f'Prenotazione {prenotazione.servizio.nome}',
                    'data': prenotazione.data_creazione,
                    'icon': 'calendar-check',
                    'color': '#28a745'
                })
            
            # Aggiungi abbonamenti recenti
            for abbonamento in abbonamenti[:2]:
                attivita_recente.append({
                    'descrizione': f'Abbonamento {abbonamento.configurazione.titolo}',
                    'data': abbonamento.data_inizio,
                    'icon': 'credit-card',
                    'color': '#007bff'
                })
            
            # Ordina per data
            attivita_recente.sort(key=lambda x: x['data'], reverse=True)
            context['attivita_recente'] = attivita_recente[:4]
            
            # Notifiche (simulata per ora)
            context['notifiche'] = []
            context['notifiche_non_lette'] = 0
            
        except Cliente.DoesNotExist:
            messages.error(self.request, 'Profilo cliente non trovato.')
            context.update({
                'prenotazioni_attive': 0,
                'abbonamenti_attivi': 0,
                'punti_fedelta': 0,
                'servizi_totali': 0,
                'prenotazioni_prossime': [],
                'abbonamenti': [],
                'attivita_recente': [],
                'notifiche': [],
                'notifiche_non_lette': 0,
            })
        
        return context