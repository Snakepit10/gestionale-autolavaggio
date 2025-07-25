from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView, TemplateView
)
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
import json
from datetime import datetime, timedelta, date
from .models import (
    ConfigurazioneAbbonamento, Abbonamento, AccessoAbbonamento,
    ServizioInclusoAbbonamento, TargaAbbonamento, ContatoreAccessiAbbonamento
)
from .forms import (
    ConfigurazioneAbbonamentoForm, AbbonamentoForm, AccessoAbbonamentoForm,
    WizardConfigurazioneBaseForm, WizardConfigurazioneTargaForm,
    WizardConfigurazioneAccessiForm, WizardConfigurazioneServiziForm
)
from apps.clienti.models import Cliente
from apps.core.models import ServizioProdotto
from apps.ordini.models import Ordine, ItemOrdine
from formtools.wizard.views import SessionWizardView


# Configurazioni Abbonamento
class ConfigurazioniAbbonamentoListView(LoginRequiredMixin, ListView):
    model = ConfigurazioneAbbonamento
    template_name = 'abbonamenti/configurazioni_list.html'
    context_object_name = 'configurazioni'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Statistiche per ogni configurazione
        for config in context['configurazioni']:
            config.abbonamenti_attivi = config.abbonamento_set.filter(stato='attivo').count()
            config.abbonamenti_totali = config.abbonamento_set.count()
            config.ricavo_totale = config.abbonamento_set.filter(
                ordine_acquisto__isnull=False
            ).aggregate(Sum('ordine_acquisto__totale_finale'))['ordine_acquisto__totale_finale__sum'] or 0
        
        return context


class WizardConfigurazioneAbbonamento(LoginRequiredMixin, SessionWizardView):
    """Wizard multi-step per creare configurazioni abbonamento"""
    
    form_list = [
        ('base', WizardConfigurazioneBaseForm),
        ('targa', WizardConfigurazioneTargaForm),
        ('accessi', WizardConfigurazioneAccessiForm),
        ('servizi', WizardConfigurazioneServiziForm),
    ]
    
    templates = {
        'base': 'abbonamenti/wizard/step_base.html',
        'targa': 'abbonamenti/wizard/step_targa.html',
        'accessi': 'abbonamenti/wizard/step_accessi.html',
        'servizi': 'abbonamenti/wizard/step_servizi.html',
    }
    
    def get_template_names(self):
        return [self.templates[self.steps.current]]
    
    def get_form_kwargs(self, step):
        kwargs = super().get_form_kwargs(step)
        if step == 'servizi':
            kwargs['servizi'] = ServizioProdotto.objects.filter(tipo='servizio', attivo=True)
        return kwargs
    
    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form, **kwargs)
        
        # Aggiungi dati per il preview
        if self.steps.current != 'base':
            cleaned_data = self.get_all_cleaned_data()
            context['preview_data'] = cleaned_data
        
        return context
    
    @transaction.atomic
    def done(self, form_list, **kwargs):
        """Crea la configurazione abbonamento con tutti i dati"""
        data = self.get_all_cleaned_data()
        
        # Crea configurazione base
        configurazione = ConfigurazioneAbbonamento.objects.create(
            titolo=data['titolo'],
            descrizione=data['descrizione'],
            prezzo=data['prezzo'],
            modalita_targa=data['modalita_targa'],
            numero_massimo_targhe=data.get('numero_massimo_targhe', 1),
            periodicita_reset=data['periodicita_reset'],
            durata=data['durata'],
            giorni_durata=self._calcola_giorni_durata(data['durata']),
            rinnovo_automatico=data.get('rinnovo_automatico', False),
            giorni_preavviso_scadenza=data.get('giorni_preavviso_scadenza', 7)
        )
        
        # Aggiungi servizi inclusi
        servizi_inclusi = data.get('servizi_inclusi', [])
        for servizio_data in servizi_inclusi:
            ServizioInclusoAbbonamento.objects.create(
                configurazione=configurazione,
                servizio_id=servizio_data['servizio_id'],
                quantita_inclusa=servizio_data['quantita'],
                accessi_totali_periodo=servizio_data.get('accessi_totali_periodo'),
                accessi_per_sottoperiodo=servizio_data.get('accessi_per_sottoperiodo'),
                tipo_sottoperiodo=servizio_data.get('tipo_sottoperiodo')
            )
        
        # Genera termini e condizioni
        configurazione.genera_termini_condizioni()
        
        messages.success(
            self.request, 
            f'Configurazione abbonamento "{configurazione.titolo}" creata con successo!'
        )
        
        return redirect('abbonamenti:config-abbonamenti-list')
    
    def _calcola_giorni_durata(self, durata):
        """Calcola i giorni basato sulla durata selezionata"""
        mapping = {
            '1_mese': 30,
            '3_mesi': 90,
            '6_mesi': 180,
            '12_mesi': 365
        }
        return mapping.get(durata, 30)


class ConfigurazioneAbbonamentoUpdateView(LoginRequiredMixin, UpdateView):
    model = ConfigurazioneAbbonamento
    form_class = ConfigurazioneAbbonamentoForm
    template_name = 'abbonamenti/configurazione_form.html'
    success_url = reverse_lazy('abbonamenti:config-abbonamenti-list')


class ConfigurazioneAbbonamentoDetailView(LoginRequiredMixin, DetailView):
    model = ConfigurazioneAbbonamento
    template_name = 'abbonamenti/configurazione_detail.html'
    context_object_name = 'configurazione'


class ConfigurazioneAbbonamentoDeleteView(LoginRequiredMixin, DeleteView):
    model = ConfigurazioneAbbonamento
    success_url = reverse_lazy('abbonamenti:config-abbonamenti-list')
    
    def delete(self, request, *args, **kwargs):
        configurazione = self.get_object()
        messages.success(request, f'Configurazione "{configurazione.titolo}" eliminata con successo.')
        return super().delete(request, *args, **kwargs)


@login_required
def dettagli_configurazione_json(request, pk):
    """Restituisce i dettagli di una configurazione in formato JSON"""
    configurazione = get_object_or_404(ConfigurazioneAbbonamento, pk=pk)
    
    # Ottieni i servizi inclusi
    servizi_inclusi = []
    for servizio_incluso in configurazione.servizi_inclusi.all():
        servizi_inclusi.append({
            'id': servizio_incluso.servizio.id,
            'titolo': servizio_incluso.servizio.titolo,
            'quantita': servizio_incluso.quantita_inclusa,
            'prezzo_singolo': float(servizio_incluso.servizio.prezzo),
            'durata_minuti': servizio_incluso.servizio.durata_minuti,
        })
    
    data = {
        'id': configurazione.id,
        'titolo': configurazione.titolo,
        'descrizione': configurazione.descrizione,
        'prezzo': float(configurazione.prezzo),
        'modalita_targa': configurazione.modalita_targa,
        'modalita_targa_display': configurazione.get_modalita_targa_display(),
        'numero_massimo_targhe': configurazione.numero_massimo_targhe,
        'periodicita_reset': configurazione.periodicita_reset,
        'periodicita_reset_display': configurazione.get_periodicita_reset_display(),
        'durata': configurazione.durata,
        'durata_display': configurazione.get_durata_display(),
        'giorni_durata': configurazione.giorni_durata,
        'rinnovo_automatico': configurazione.rinnovo_automatico,
        'giorni_preavviso_scadenza': configurazione.giorni_preavviso_scadenza,
        'servizi_inclusi': servizi_inclusi,
        'termini_condizioni': configurazione.termini_condizioni,
    }
    
    return JsonResponse(data)


@login_required
def clona_configurazione(request, pk):
    """Clona una configurazione esistente"""
    configurazione = get_object_or_404(ConfigurazioneAbbonamento, pk=pk)
    
    # Crea una copia
    nuova_configurazione = ConfigurazioneAbbonamento.objects.create(
        titolo=f"{configurazione.titolo} (Copia)",
        descrizione=configurazione.descrizione,
        prezzo=configurazione.prezzo,
        modalita_targa=configurazione.modalita_targa,
        numero_massimo_targhe=configurazione.numero_massimo_targhe,
        periodicita_reset=configurazione.periodicita_reset,
        durata=configurazione.durata,
        giorni_durata=configurazione.giorni_durata,
        rinnovo_automatico=configurazione.rinnovo_automatico,
        giorni_preavviso_scadenza=configurazione.giorni_preavviso_scadenza,
        attiva=False  # Disattivata di default
    )
    
    # Copia servizi inclusi
    for servizio_incluso in configurazione.servizi_inclusi.all():
        ServizioInclusoAbbonamento.objects.create(
            configurazione=nuova_configurazione,
            servizio=servizio_incluso.servizio,
            quantita_inclusa=servizio_incluso.quantita_inclusa
        )
    
    # Genera termini e condizioni
    nuova_configurazione.genera_termini_condizioni()
    
    messages.success(request, f'Configurazione clonata con successo: {nuova_configurazione.titolo}')
    return redirect('abbonamenti:config-abbonamento-update', pk=nuova_configurazione.pk)


# Gestione Abbonamenti
class AbbonamentiListView(LoginRequiredMixin, ListView):
    model = Abbonamento
    template_name = 'abbonamenti/abbonamenti_list.html'
    context_object_name = 'abbonamenti'
    paginate_by = 50
    
    def get_queryset(self):
        queryset = Abbonamento.objects.select_related('cliente', 'configurazione')
        
        # Filtri
        stato = self.request.GET.get('stato')
        configurazione = self.request.GET.get('configurazione')
        cliente = self.request.GET.get('cliente')
        
        if stato:
            queryset = queryset.filter(stato=stato)
        if configurazione:
            queryset = queryset.filter(configurazione_id=configurazione)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        
        return queryset.order_by('-data_attivazione')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['configurazioni'] = ConfigurazioneAbbonamento.objects.filter(attiva=True)
        context['totale_abbonamenti'] = Abbonamento.objects.count()
        context['abbonamenti_attivi'] = Abbonamento.objects.filter(stato='attivo').count()
        context['abbonamenti_in_scadenza'] = Abbonamento.objects.filter(
            stato='attivo',
            data_scadenza__lte=date.today() + timedelta(days=7)
        ).count()
        return context


class VenditaAbbonamentoView(LoginRequiredMixin, TemplateView):
    """Vista per vendita nuovo abbonamento"""
    template_name = 'abbonamenti/vendita_abbonamento.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['configurazioni'] = ConfigurazioneAbbonamento.objects.filter(attiva=True)
        context['clienti'] = Cliente.objects.all().order_by('cognome', 'ragione_sociale', 'nome')
        return context
    
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        try:
            # Debug logging
            print(f"POST data: {dict(request.POST)}")
            
            cliente_id = request.POST.get('cliente_id')
            configurazione_id = request.POST.get('configurazione_id')
            targhe = request.POST.getlist('targhe')
            
            cliente = get_object_or_404(Cliente, id=cliente_id)
            configurazione = get_object_or_404(ConfigurazioneAbbonamento, id=configurazione_id)
            
            # Crea abbonamento
            abbonamento = Abbonamento.objects.create(
                cliente=cliente,
                configurazione=configurazione
            )
            
            # Aggiungi targhe se richieste
            if configurazione.modalita_targa != 'libera':
                for targa in targhe:
                    if targa.strip():
                        TargaAbbonamento.objects.create(
                            abbonamento=abbonamento,
                            targa=targa.strip().upper()
                        )
            
            # Crea ordine per il pagamento
            ordine = Ordine.objects.create(
                cliente=cliente,
                origine='abbonamento',
                totale=configurazione.prezzo,
                totale_finale=configurazione.prezzo,
                stato_pagamento='non_pagato',
                metodo_pagamento=request.POST.get('metodo_pagamento', 'contanti'),
                nota=f'Abbonamento {configurazione.titolo}'[:250],  # Limita la lunghezza
                operatore=request.user
            )
            
            # Collega ordine all'abbonamento
            abbonamento.ordine_acquisto = ordine
            abbonamento.save()
            
            # Registra pagamento se richiesto
            try:
                importo_pagamento = float(request.POST.get('importo_pagamento', 0) or 0)
            except (ValueError, TypeError):
                importo_pagamento = 0
            
            if importo_pagamento > 0:
                from apps.ordini.models import Pagamento
                Pagamento.objects.create(
                    ordine=ordine,
                    importo=importo_pagamento,
                    metodo=request.POST.get('metodo_pagamento', 'contanti'),
                    operatore=request.user
                )
            
            messages.success(
                request,
                f'Abbonamento {abbonamento.codice_accesso} creato per {cliente}!'
            )
            
            return redirect('abbonamenti:dettaglio-abbonamento', codice=abbonamento.codice_accesso)
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"Error creating abbonamento: {error_detail}")  # For debugging
            messages.error(request, f'Errore nella creazione abbonamento: {str(e)}')
            return self.get(request, *args, **kwargs)


class DettaglioAbbonamentoView(LoginRequiredMixin, DetailView):
    model = Abbonamento
    template_name = 'abbonamenti/dettaglio_abbonamento.html'
    context_object_name = 'abbonamento'
    
    def get_object(self, queryset=None):
        codice = self.kwargs.get('codice')
        return get_object_or_404(Abbonamento, codice_accesso=codice)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        abbonamento = self.object
        
        # Contatori accessi per servizio
        context['contatori_servizi'] = []
        for servizio_incluso in abbonamento.configurazione.servizi_inclusi.all():
            contatore = abbonamento.get_contatore_corrente(servizio_incluso.servizio)
            disponibili = servizio_incluso.quantita_inclusa - contatore.accessi_effettuati
            
            context['contatori_servizi'].append({
                'servizio': servizio_incluso.servizio,
                'inclusi': servizio_incluso.quantita_inclusa,
                'utilizzati': contatore.accessi_effettuati,
                'disponibili': disponibili,
                'contatore': contatore
            })
        
        # Ultimi accessi
        context['ultimi_accessi'] = abbonamento.accessi.order_by('-data_ora')[:10]
        
        # Targhe associate
        context['targhe'] = abbonamento.targhe.filter(attiva=True)
        
        return context


class AbbonamentiInScadenzaView(LoginRequiredMixin, ListView):
    model = Abbonamento
    template_name = 'abbonamenti/abbonamenti_scadenza.html'
    context_object_name = 'abbonamenti'
    
    def get_queryset(self):
        data_limite = date.today() + timedelta(days=30)
        return Abbonamento.objects.filter(
            stato='attivo',
            data_scadenza__lte=data_limite
        ).select_related('cliente', 'configurazione').order_by('data_scadenza')


# Verifica Accessi NFC
class VerificaAccessoView(LoginRequiredMixin, TemplateView):
    """Pagina principale per verifica accessi"""
    template_name = 'abbonamenti/verifica_accesso.html'


class VerificaAbbonamentoView(DetailView):
    """Vista pubblica per verifica abbonamento tramite codice NFC"""
    model = Abbonamento
    template_name = 'abbonamenti/verifica_abbonamento.html'
    slug_field = 'codice_nfc'
    slug_url_kwarg = 'codice'
    context_object_name = 'abbonamento'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        abbonamento = self.object
        
        # Servizi disponibili con contatori
        servizi_disponibili = []
        for servizio_incluso in abbonamento.configurazione.servizi_inclusi.all():
            contatore = abbonamento.get_contatore_corrente(servizio_incluso.servizio)
            disponibili = servizio_incluso.quantita_inclusa - contatore.accessi_effettuati
            
            servizi_disponibili.append({
                'servizio': servizio_incluso.servizio,
                'totali': servizio_incluso.quantita_inclusa,
                'utilizzati': contatore.accessi_effettuati,
                'disponibili': disponibili
            })
        
        context['servizi_disponibili'] = servizi_disponibili
        context['può_accedere'] = abbonamento.stato == 'attivo'
        
        # Targhe se richieste
        if abbonamento.configurazione.modalita_targa != 'libera':
            context['targhe_autorizzate'] = abbonamento.targhe.filter(attiva=True)
        
        return context


@csrf_exempt
@transaction.atomic
def registra_accesso_abbonamento(request, codice):
    """API per registrare un accesso abbonamento"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        data = json.loads(request.body)
        
        # Cerca prima per codice_accesso, poi per codice_nfc
        try:
            abbonamento = Abbonamento.objects.get(codice_accesso=codice)
        except Abbonamento.DoesNotExist:
            abbonamento = get_object_or_404(Abbonamento, codice_nfc=codice)
        
        servizio_id = data.get('servizio_id')
        servizio = get_object_or_404(ServizioProdotto, id=servizio_id)
        targa = data.get('targa', '').upper()
        metodo_verifica = data.get('metodo', 'nfc')
        crea_ordine = data.get('crea_ordine', False)
        
        # Verifica disponibilità
        autorizzato, motivo = abbonamento.verifica_accesso_disponibile(servizio, targa)
        
        # Debug logging
        print(f"Debug accesso - Codice: {codice}, Servizio: {servizio.titolo}, Targa: {targa}")
        print(f"Abbonamento stato: {abbonamento.stato}, Scadenza: {abbonamento.data_scadenza}")
        print(f"Autorizzato: {autorizzato}, Motivo: {motivo}")
        
        # Registra accesso
        accesso = AccessoAbbonamento.objects.create(
            abbonamento=abbonamento,
            servizio=servizio,
            targa_utilizzata=targa,
            postazione=getattr(request.user, 'postazione', None) if hasattr(request.user, 'postazione') else None,
            metodo_verifica=metodo_verifica,
            autorizzato=autorizzato,
            motivo_rifiuto=motivo if not autorizzato else '',
            operatore=request.user if request.user.is_authenticated else None
        )
        
        if autorizzato:
            # Aggiorna contatore
            contatore = abbonamento.get_contatore_corrente(servizio)
            contatore.accessi_effettuati += 1
            contatore.save()
            
            # Aggiorna ultimo accesso
            abbonamento.data_ultimo_accesso = timezone.now()
            abbonamento.save()
            
            # Crea ordine automatico se richiesto
            if crea_ordine:
                ordine = Ordine.objects.create(
                    cliente=abbonamento.cliente,
                    origine='abbonamento',
                    totale=0,  # gratis per abbonato
                    totale_finale=0,
                    stato_pagamento='pagato',
                    metodo_pagamento='abbonamento',
                    nota=f'Accesso abbonamento #{abbonamento.codice_accesso}',
                    operatore=request.user if request.user.is_authenticated else None
                )
                
                ItemOrdine.objects.create(
                    ordine=ordine,
                    servizio_prodotto=servizio,
                    quantita=1,
                    prezzo_unitario=0,
                    postazione_assegnata=servizio.postazioni.first()
                )
                
                # TODO: Notifica dashboard postazione (quando channels sarà installato)
                # Commento temporaneo per evitare errore "No module named 'channels'"
        
        return JsonResponse({
            'autorizzato': autorizzato,
            'motivo': motivo,
            'accesso_id': accesso.id,
            'contatori_aggiornati': autorizzato
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Dati JSON non validi'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def rinnova_abbonamento(request, codice):
    """Rinnova un abbonamento scaduto"""
    abbonamento = get_object_or_404(Abbonamento, codice_accesso=codice)
    
    if abbonamento.stato != 'scaduto':
        messages.error(request, 'Solo gli abbonamenti scaduti possono essere rinnovati')
        return redirect('abbonamenti:dettaglio-abbonamento', codice=codice)
    
    try:
        with transaction.atomic():
            # Crea nuovo abbonamento
            nuovo_abbonamento = Abbonamento.objects.create(
                cliente=abbonamento.cliente,
                configurazione=abbonamento.configurazione
            )
            
            # Copia targhe dal vecchio abbonamento
            for targa in abbonamento.targhe.filter(attiva=True):
                TargaAbbonamento.objects.create(
                    abbonamento=nuovo_abbonamento,
                    targa=targa.targa
                )
            
            # Crea ordine per il pagamento
            ordine = Ordine.objects.create(
                cliente=abbonamento.cliente,
                origine='abbonamento',
                totale=abbonamento.configurazione.prezzo,
                totale_finale=abbonamento.configurazione.prezzo,
                stato_pagamento='non_pagato',
                metodo_pagamento='contanti',
                nota=f'Rinnovo abbonamento {abbonamento.configurazione.titolo}',
                operatore=request.user
            )
            
            nuovo_abbonamento.ordine_acquisto = ordine
            nuovo_abbonamento.save()
            
            messages.success(
                request,
                f'Abbonamento rinnovato! Nuovo codice: {nuovo_abbonamento.codice_accesso}'
            )
            
            return redirect('abbonamenti:dettaglio-abbonamento', codice=nuovo_abbonamento.codice_accesso)
            
    except Exception as e:
        messages.error(request, f'Errore nel rinnovo: {str(e)}')
        return redirect('abbonamenti:dettaglio-abbonamento', codice=codice)


# API JSON Endpoints per verifica accesso
@csrf_exempt
def verifica_abbonamento_json(request, codice):
    """API JSON per verificare abbonamento via codice"""
    try:
        # Cerca prima per codice_accesso, poi per codice_nfc
        try:
            abbonamento = Abbonamento.objects.get(codice_accesso=codice)
        except Abbonamento.DoesNotExist:
            try:
                abbonamento = Abbonamento.objects.get(codice_nfc=codice)
            except Abbonamento.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'Abbonamento con codice "{codice}" non trovato'
                })
        
        # Servizi disponibili con contatori
        servizi_disponibili = []
        for servizio_incluso in abbonamento.configurazione.servizi_inclusi.all():
            contatore = abbonamento.get_contatore_corrente(servizio_incluso.servizio)
            disponibili = servizio_incluso.quantita_inclusa - contatore.accessi_effettuati
            percentuale_usata = (contatore.accessi_effettuati / servizio_incluso.quantita_inclusa) * 100 if servizio_incluso.quantita_inclusa > 0 else 0
            
            servizi_disponibili.append({
                'id': servizio_incluso.servizio.id,
                'nome': servizio_incluso.servizio.titolo,
                'totali': servizio_incluso.quantita_inclusa,
                'utilizzati': contatore.accessi_effettuati,
                'disponibili': disponibili,
                'percentuale': round(percentuale_usata, 1)
            })
        
        # Targhe autorizzate
        targhe_autorizzate = []
        if abbonamento.configurazione.modalita_targa != 'libera':
            targhe_autorizzate = [targa.targa for targa in abbonamento.targhe.filter(attiva=True)]
        
        data = {
            'success': True,
            'abbonamento': {
                'codice': abbonamento.codice_nfc,
                'stato': abbonamento.stato,
                'configurazione': abbonamento.configurazione.titolo,
                'data_scadenza': abbonamento.data_scadenza.strftime('%d/%m/%Y'),
                'scaduto': abbonamento.data_scadenza < date.today(),
                'cliente': {
                    'nome': str(abbonamento.cliente),
                    'email': abbonamento.cliente.email or 'Non specificata',
                    'telefono': abbonamento.cliente.telefono
                },
                'servizi_disponibili': servizi_disponibili,
                'targhe_autorizzate': targhe_autorizzate,
                'modalita_targa': abbonamento.configurazione.modalita_targa
            }
        }
        
        return JsonResponse(data)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Abbonamento non trovato o non valido: {str(e)}'
        })


@login_required
def statistiche_oggi_json(request):
    """API JSON per statistiche di oggi"""
    oggi = date.today()
    inizio_oggi = datetime.combine(oggi, datetime.min.time())
    fine_oggi = datetime.combine(oggi, datetime.max.time())
    
    # Statistiche accessi
    accessi_oggi = AccessoAbbonamento.objects.filter(
        data_ora__range=[inizio_oggi, fine_oggi]
    )
    
    accessi_autorizzati = accessi_oggi.filter(autorizzato=True).count()
    accessi_negati = accessi_oggi.filter(autorizzato=False).count()
    
    # Statistiche abbonamenti
    abbonamenti_attivi = Abbonamento.objects.filter(stato='attivo').count()
    abbonamenti_in_scadenza = Abbonamento.objects.filter(
        stato='attivo',
        data_scadenza__lte=oggi + timedelta(days=7)
    ).count()
    
    data = {
        'accessi_oggi': accessi_autorizzati,
        'accessi_negati': accessi_negati,
        'abbonamenti_attivi': abbonamenti_attivi,
        'in_scadenza': abbonamenti_in_scadenza
    }
    
    return JsonResponse(data)


@login_required
def ultimi_accessi_json(request):
    """API JSON per ultimi accessi"""
    oggi = date.today()
    inizio_oggi = datetime.combine(oggi, datetime.min.time())
    fine_oggi = datetime.combine(oggi, datetime.max.time())
    
    accessi = AccessoAbbonamento.objects.filter(
        data_ora__range=[inizio_oggi, fine_oggi]
    ).select_related(
        'abbonamento__cliente', 
        'servizio'
    ).order_by('-data_ora')[:20]
    
    accessi_data = []
    for accesso in accessi:
        accessi_data.append({
            'cliente': str(accesso.abbonamento.cliente),
            'servizio': accesso.servizio.titolo,
            'data_ora': accesso.data_ora.strftime('%H:%M'),
            'targa': accesso.targa_utilizzata,
            'autorizzato': accesso.autorizzato,
            'motivo': accesso.motivo_rifiuto if not accesso.autorizzato else None
        })
    
    return JsonResponse({'accessi': accessi_data})


@login_required
def debug_abbonamenti_json(request):
    """API di debug per vedere abbonamenti esistenti - RIMUOVI IN PRODUZIONE"""
    abbonamenti = Abbonamento.objects.all()[:10]  # Solo i primi 10
    
    abbonamenti_data = []
    for abbonamento in abbonamenti:
        abbonamenti_data.append({
            'id': abbonamento.id,
            'codice_accesso': abbonamento.codice_accesso,
            'codice_nfc': abbonamento.codice_nfc,
            'cliente': str(abbonamento.cliente),
            'configurazione': abbonamento.configurazione.titolo,
            'stato': abbonamento.stato,
            'data_scadenza': abbonamento.data_scadenza.strftime('%Y-%m-%d')
        })
    
    return JsonResponse({'abbonamenti': abbonamenti_data, 'totale': Abbonamento.objects.count()})


@login_required
def debug_abbonamento_dettagli_json(request, codice):
    """API di debug per dettagli specifici abbonamento - RIMUOVI IN PRODUZIONE"""
    try:
        # Cerca prima per codice_accesso, poi per codice_nfc
        try:
            abbonamento = Abbonamento.objects.get(codice_accesso=codice)
        except Abbonamento.DoesNotExist:
            try:
                abbonamento = Abbonamento.objects.get(codice_nfc=codice)
            except Abbonamento.DoesNotExist:
                return JsonResponse({'error': f'Abbonamento "{codice}" non trovato'})
        
        # Ottieni tutti i servizi inclusi con i contatori
        servizi_dettagli = []
        for servizio_incluso in abbonamento.configurazione.servizi_inclusi.all():
            contatore = abbonamento.get_contatore_corrente(servizio_incluso.servizio)
            
            # Test di verifica accesso per ogni servizio
            autorizzato, motivo = abbonamento.verifica_accesso_disponibile(servizio_incluso.servizio)
            
            servizi_dettagli.append({
                'servizio': servizio_incluso.servizio.titolo,
                'servizio_id': servizio_incluso.servizio.id,
                'quantita_inclusa': servizio_incluso.quantita_inclusa,
                'accessi_effettuati': contatore.accessi_effettuati,
                'disponibili': servizio_incluso.quantita_inclusa - contatore.accessi_effettuati,
                'accessi_totali_periodo': servizio_incluso.accessi_totali_periodo,
                'accessi_per_sottoperiodo': servizio_incluso.accessi_per_sottoperiodo,
                'tipo_sottoperiodo': servizio_incluso.tipo_sottoperiodo,
                'test_accesso_autorizzato': autorizzato,
                'test_accesso_motivo': motivo
            })
        
        # Targhe autorizzate
        targhe = []
        for targa in abbonamento.targhe.filter(attiva=True):
            targhe.append({
                'targa': targa.targa,
                'attiva': targa.attiva
            })
        
        # Ultimi accessi
        ultimi_accessi = []
        for accesso in abbonamento.accessi.order_by('-data_ora')[:5]:
            ultimi_accessi.append({
                'data_ora': accesso.data_ora.strftime('%Y-%m-%d %H:%M:%S'),
                'servizio': accesso.servizio.titolo,
                'autorizzato': accesso.autorizzato,
                'motivo_rifiuto': accesso.motivo_rifiuto,
                'targa': accesso.targa_utilizzata
            })
        
        data = {
            'abbonamento': {
                'id': abbonamento.id,
                'codice_accesso': abbonamento.codice_accesso,
                'codice_nfc': abbonamento.codice_nfc,
                'cliente': str(abbonamento.cliente),
                'configurazione': abbonamento.configurazione.titolo,
                'stato': abbonamento.stato,
                'data_attivazione': abbonamento.data_attivazione.strftime('%Y-%m-%d'),
                'data_scadenza': abbonamento.data_scadenza.strftime('%Y-%m-%d'),
                'scaduto': abbonamento.data_scadenza < date.today(),
                'modalita_targa': abbonamento.configurazione.modalita_targa
            },
            'servizi': servizi_dettagli,
            'targhe': targhe,
            'ultimi_accessi': ultimi_accessi
        }
        
        return JsonResponse(data)
        
    except Exception as e:
        return JsonResponse({'error': str(e)})


@login_required
def sospendi_abbonamento(request, codice):
    """Sospende temporaneamente un abbonamento"""
    abbonamento = get_object_or_404(Abbonamento, codice_accesso=codice)
    
    if request.method == 'POST':
        motivo = request.POST.get('motivo', '')
        
        if abbonamento.stato == 'attivo':
            abbonamento.stato = 'sospeso'
            abbonamento.save()
            
            # Log del motivo (potresti creare un modello per questo)
            messages.success(request, f'Abbonamento sospeso. Motivo: {motivo}')
        else:
            messages.error(request, 'Solo abbonamenti attivi possono essere sospesi')
    
    return redirect('abbonamenti:dettaglio-abbonamento', codice=codice)