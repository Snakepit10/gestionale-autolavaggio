from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
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
        
        if user is not None and (user.is_staff or user.groups.exists()):
            login(request, user)
            
            # Gestisci "ricordami"
            if not remember_me:
                request.session.set_expiry(0)  # Scade alla chiusura del browser
            else:
                request.session.set_expiry(1209600)  # 2 settimane
            
            messages.success(request, f'Benvenuto, {user.get_full_name() or user.username}!')
            
            # Redirect: operatori alla selezione postazioni, altri alla home
            default_url = 'core:home'
            if user.groups.filter(name='operatore').exists():
                default_url = 'turni:selezione_postazioni'
            next_url = request.GET.get('next', default_url)
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


@login_required(login_url='/auth/clienti/login/')
def completa_profilo(request):
    """Step obbligatorio post-registrazione Google: telefono mancante.

    Google fornisce nome/cognome/email ma non il numero di telefono,
    che per noi e' obbligatorio (notifiche WhatsApp, anti-duplicati).
    Il CompletamentoProfiloMiddleware rimanda qui i clienti loggati
    senza telefono finche' non lo salvano.
    """
    cliente = getattr(request.user, 'cliente', None)
    if cliente is None:
        return redirect('auth:client-login')
    if (cliente.telefono or '').strip():
        return redirect('clients:dashboard')

    if request.method == 'POST':
        from apps.clienti.utils import normalizza_telefono, valuta_collegamento_telefono
        telefono = (request.POST.get('telefono') or '').strip()

        if not normalizza_telefono(telefono):
            messages.error(request, 'Numero di telefono non valido: ricontrollalo.')
        else:
            # Stessa regola di tutte le registrazioni: numero gia' in
            # anagrafica -> verifica del nominativo (>= 90% di
            # somiglianza, dati Google vs scheda). Se combacia colleghiamo
            # la scheda esistente (storico/punti preservati), altrimenti
            # serve lo sblocco dell'operatore.
            esito, esistente = valuta_collegamento_telefono(
                telefono, cliente.nome, cliente.cognome, escludi_pk=cliente.pk)

            if esito == 'occupato':
                messages.error(
                    request,
                    'Questo numero risulta gia\' collegato a un altro account. '
                    'Se e\' il tuo, chiamaci o scrivici al 379 233 7051 per lo sblocco.'
                )
            elif esito == 'verifica_fallita':
                messages.error(
                    request,
                    'Questo numero risulta gia\' in anagrafica con un altro '
                    'nominativo. Chiamaci o scrivici al 379 233 7051 per lo sblocco.'
                )
            elif esito == 'collega':
                # Merge nella scheda storica via unisci_clienti (pulizia
                # clienti): sposta lo user, completa i campi vuoti con i
                # dati Google, elimina la scheda vuota appena creata.
                from apps.clienti.services_pulizia import unisci_clienti
                unisci_clienti(esistente, [cliente])
                messages.success(
                    request,
                    f'Bentornato, {esistente.nome_completo}! Abbiamo collegato il '
                    f'tuo account alla tua scheda cliente: ritrovi storico e punti.'
                )
                return redirect('clients:dashboard')
            else:  # libero
                cliente.telefono = telefono
                cliente.save(update_fields=['telefono'])
                messages.success(request, f'Benvenuto, {cliente.nome_completo}! Profilo completato.')
                return redirect('clients:dashboard')

    return render(request, 'auth/client_completa_profilo.html', {'cliente': cliente})


# ---------------------------------------------------------------------
# Recupero password clienti
#
# Flusso standard Django (token monouso con scadenza PASSWORD_RESET_TIMEOUT,
# default 3 giorni) con template brandizzati MasterWash. L'email del form
# viene confrontata con User.email (alla registrazione username = email =
# User.email, quindi il match e' affidabile). Se l'email non corrisponde a
# nessun account la pagina "inviata" e' identica: non riveliamo quali email
# sono registrate.
# ---------------------------------------------------------------------

class ClientPasswordResetView(auth_views.PasswordResetView):
    """Step 1: il cliente inserisce l'email e riceve il link di reset."""
    template_name = 'auth/client_password_reset.html'
    email_template_name = 'auth/emails/password_reset_email.txt'
    subject_template_name = 'auth/emails/password_reset_subject.txt'
    success_url = reverse_lazy('auth:client-password-reset-done')


class ClientPasswordResetDoneView(auth_views.PasswordResetDoneView):
    """Step 2: conferma 'se l'email esiste, ti abbiamo scritto'."""
    template_name = 'auth/client_password_reset_done.html'


class ClientPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Step 3: dal link nell'email, form nuova password."""
    template_name = 'auth/client_password_reset_confirm.html'
    success_url = reverse_lazy('auth:client-password-reset-complete')


class ClientPasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    """Step 4: password cambiata, invito al login."""
    template_name = 'auth/client_password_reset_complete.html'


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