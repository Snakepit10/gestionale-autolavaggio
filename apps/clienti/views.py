from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView, TemplateView
)
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings
import csv
import json
from .models import Cliente, PuntiFedelta, MovimentoPunti
from .forms import ClienteForm, ClienteSearchForm
from apps.ordini.models import Ordine


class ClientiListView(LoginRequiredMixin, ListView):
    model = Cliente
    template_name = 'clienti/clienti_list.html'
    context_object_name = 'clienti'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Cliente.objects.annotate(
            ordini_count=Count('ordine'),
            spesa_totale=Sum('ordine__totale_finale')
        )
        
        # Filtri
        search = self.request.GET.get('search')
        tipo = self.request.GET.get('tipo')
        
        if search:
            queryset = queryset.filter(
                Q(nome__icontains=search) |
                Q(cognome__icontains=search) |
                Q(ragione_sociale__icontains=search) |
                Q(email__icontains=search) |
                Q(telefono__icontains=search) |
                Q(partita_iva__icontains=search) |
                Q(codice_fiscale__icontains=search)
            )
        
        if tipo:
            queryset = queryset.filter(tipo=tipo)
        
        return queryset.order_by('-data_registrazione')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = ClienteSearchForm(self.request.GET)
        context['total_clienti'] = Cliente.objects.count()
        context['clienti_privati'] = Cliente.objects.filter(tipo='privato').count()
        context['clienti_aziende'] = Cliente.objects.filter(tipo='azienda').count()
        return context


class ClienteCreateView(LoginRequiredMixin, CreateView):
    model = Cliente
    form_class = ClienteForm
    template_name = 'clienti/cliente_form.html'
    success_url = reverse_lazy('clienti:clienti-list')
    
    def form_valid(self, form):
        # Crea automaticamente il record punti fedeltà
        response = super().form_valid(form)
        PuntiFedelta.objects.get_or_create(cliente=self.object)
        
        messages.success(self.request, f'Cliente "{self.object}" creato con successo!')
        
        # Se richiesto, crea anche l'account utente
        if form.cleaned_data.get('crea_account_online'):
            self.crea_account_utente()
        
        return response
    
    def crea_account_utente(self):
        """Crea un account utente per l'accesso online"""
        try:
            # Genera username dal nome/email
            if self.object.tipo == 'privato':
                username = f"{self.object.nome.lower()}.{self.object.cognome.lower()}"
            else:
                username = self.object.email.split('@')[0]
            
            # Verifica unicità username
            counter = 1
            original_username = username
            while User.objects.filter(username=username).exists():
                username = f"{original_username}{counter}"
                counter += 1
            
            # Crea utente
            user = User.objects.create_user(
                username=username,
                email=self.object.email,
                first_name=self.object.nome,
                last_name=self.object.cognome
            )
            
            # Collega al cliente
            self.object.user = user
            self.object.save()
            
            # Invia email con credenziali (da implementare)
            # send_credenziali_email(self.object, password)
            
            messages.success(
                self.request, 
                f'Account online creato con username: {username}'
            )
            
        except Exception as e:
            messages.warning(
                self.request, 
                f'Cliente creato ma errore nella creazione account: {str(e)}'
            )


class ClienteUpdateView(LoginRequiredMixin, UpdateView):
    model = Cliente
    form_class = ClienteForm
    template_name = 'clienti/cliente_form.html'
    success_url = reverse_lazy('clienti:clienti-list')
    
    def form_valid(self, form):
        messages.success(self.request, f'Cliente "{form.instance}" aggiornato con successo!')
        return super().form_valid(form)


class ClienteDeleteView(LoginRequiredMixin, DeleteView):
    model = Cliente
    template_name = 'clienti/cliente_confirm_delete.html'
    success_url = reverse_lazy('clienti:clienti-list')
    
    def delete(self, request, *args, **kwargs):
        cliente = self.get_object()
        messages.success(request, f'Cliente "{cliente}" eliminato con successo!')
        return super().delete(request, *args, **kwargs)


class StoricoClienteView(LoginRequiredMixin, DetailView):
    model = Cliente
    template_name = 'clienti/storico_cliente.html'
    context_object_name = 'cliente'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.object
        
        # Ordini del cliente
        context['ordini'] = Ordine.objects.filter(cliente=cliente).order_by('-data_ora')[:20]
        
        # Statistiche
        context['statistiche'] = {
            'ordini_totali': cliente.get_ordini_totali(),
            'spesa_totale': cliente.get_spesa_totale(),
            'spesa_media': cliente.get_spesa_totale() / max(cliente.get_ordini_totali(), 1),
            'ultimo_ordine': cliente.ordine_set.order_by('-data_ora').first(),
        }
        
        # Punti fedeltà
        punti_fedelta, created = PuntiFedelta.objects.get_or_create(cliente=cliente)
        context['punti_fedelta'] = punti_fedelta
        context['movimenti_punti'] = MovimentoPunti.objects.filter(
            cliente=cliente
        ).order_by('-data_movimento')[:10]
        
        # Abbonamenti attivi
        context['abbonamenti_attivi'] = cliente.abbonamenti.filter(stato='attivo')
        
        return context


@login_required
def cerca_cliente(request):
    """AJAX endpoint per ricerca clienti"""
    term = request.GET.get('term', '')
    
    if len(term) < 2:
        return JsonResponse({'results': []})
    
    clienti = Cliente.objects.filter(
        Q(nome__icontains=term) |
        Q(cognome__icontains=term) |
        Q(ragione_sociale__icontains=term) |
        Q(email__icontains=term) |
        Q(telefono__icontains=term)
    )[:10]
    
    results = []
    for cliente in clienti:
        results.append({
            'id': cliente.id,
            'text': str(cliente),
            'email': cliente.email,
            'telefono': cliente.telefono,
            'tipo': cliente.tipo
        })
    
    return JsonResponse({'results': results})


@login_required
def invia_credenziali_cliente(request, pk):
    """Invia le credenziali di accesso al cliente via email"""
    cliente = get_object_or_404(Cliente, pk=pk)
    
    if not cliente.user:
        messages.error(request, 'Questo cliente non ha un account online')
        return redirect('clienti:storico-cliente', pk=pk)
    
    try:
        # Genera nuova password temporanea
        import secrets
        import string
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
        
        # Aggiorna password utente
        cliente.user.set_password(password)
        cliente.user.save()
        
        # Invia email (da implementare template)
        subject = 'Credenziali accesso - Autolavaggio'
        message = f"""
        Gentile {cliente.nome_completo},
        
        Le sue credenziali per accedere all'area riservata sono:
        
        Username: {cliente.user.username}
        Password: {password}
        
        Può accedere all'indirizzo: {request.build_absolute_uri('/area-cliente/')}
        
        La preghiamo di cambiare la password al primo accesso.
        
        Cordiali saluti,
        Il team Autolavaggio
        """
        
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [cliente.email],
            fail_silently=False,
        )
        
        messages.success(request, f'Credenziali inviate via email a {cliente.email}')
        
    except Exception as e:
        messages.error(request, f'Errore nell\'invio email: {str(e)}')
    
    return redirect('clienti:storico-cliente', pk=pk)


@login_required
def export_clienti_csv(request):
    """Esporta la lista clienti in CSV"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="clienti_export.csv"'
    
    writer = csv.writer(response)
    writer.writerow([
        'Tipo', 'Nome/Ragione Sociale', 'Email', 'Telefono', 
        'Indirizzo', 'CAP', 'Città', 'Codice Fiscale/P.IVA',
        'Data Registrazione', 'Ordini Totali', 'Spesa Totale'
    ])
    
    clienti = Cliente.objects.annotate(
        ordini_count=Count('ordine'),
        spesa_totale=Sum('ordine__totale_finale')
    )
    
    for cliente in clienti:
        if cliente.tipo == 'privato':
            nome = f"{cliente.nome} {cliente.cognome}"
            codice = cliente.codice_fiscale
        else:
            nome = cliente.ragione_sociale
            codice = cliente.partita_iva
        
        writer.writerow([
            cliente.get_tipo_display(),
            nome,
            cliente.email,
            cliente.telefono,
            cliente.indirizzo,
            cliente.cap,
            cliente.citta,
            codice,
            cliente.data_registrazione.strftime('%d/%m/%Y'),
            cliente.ordini_count or 0,
            cliente.spesa_totale or 0
        ])
    
    return response


@login_required
def gestisci_punti_fedelta(request, pk):
    """Gestisce i punti fedeltà di un cliente"""
    cliente = get_object_or_404(Cliente, pk=pk)
    punti_fedelta, created = PuntiFedelta.objects.get_or_create(cliente=cliente)
    
    if request.method == 'POST':
        azione = request.POST.get('azione')
        punti = int(request.POST.get('punti', 0))
        descrizione = request.POST.get('descrizione', '')
        
        if azione == 'aggiungi':
            punti_fedelta.punti_totali += punti
            punti_fedelta.save()
            
            MovimentoPunti.objects.create(
                cliente=cliente,
                tipo='bonus',
                punti=punti,
                descrizione=descrizione or f'Bonus punti manuale (+{punti})'
            )
            
            messages.success(request, f'Aggiunti {punti} punti al cliente')
            
        elif azione == 'sottrai':
            if punti_fedelta.punti_disponibili >= punti:
                punti_fedelta.punti_utilizzati += punti
                punti_fedelta.save()
                
                MovimentoPunti.objects.create(
                    cliente=cliente,
                    tipo='utilizzo',
                    punti=-punti,
                    descrizione=descrizione or f'Utilizzo punti manuale (-{punti})'
                )
                
                messages.success(request, f'Sottratti {punti} punti al cliente')
            else:
                messages.error(request, 'Punti insufficienti')
    
    return redirect('clienti:storico-cliente', pk=pk)