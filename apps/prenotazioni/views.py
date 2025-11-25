from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView, TemplateView
)
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse
from django.contrib import messages
from django.db.models import Q, Count, F
from django.utils import timezone
from django.db import transaction
import json
from datetime import datetime, timedelta, date, time
from .models import (
    ConfigurazioneSlot, SlotPrenotazione, Prenotazione, CalendarioPersonalizzato
)
from .forms import ConfigurazioneSlotForm, PrenotazioneForm
from apps.clienti.models import Cliente
from apps.core.models import ServizioProdotto
from apps.abbonamenti.models import Abbonamento
from apps.ordini.models import Ordine


# Configurazione Slot
class ConfigurazioneSlotListView(LoginRequiredMixin, ListView):
    model = ConfigurazioneSlot
    template_name = 'prenotazioni/config_slot_list.html'
    context_object_name = 'configurazioni'


class ConfigurazioneSlotCreateView(LoginRequiredMixin, CreateView):
    model = ConfigurazioneSlot
    form_class = ConfigurazioneSlotForm
    template_name = 'prenotazioni/config_slot_form.html'
    success_url = reverse_lazy('prenotazioni:config-slot-list')


class ConfigurazioneSlotUpdateView(LoginRequiredMixin, UpdateView):
    model = ConfigurazioneSlot
    form_class = ConfigurazioneSlotForm
    template_name = 'prenotazioni/config_slot_form.html'
    success_url = reverse_lazy('prenotazioni:config-slot-list')


# Prenotazioni Cliente
class PrenotazioniView(TemplateView):
    """Vista principale prenotazioni per clienti e operatori"""
    template_name = 'prenotazioni/prenotazioni.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        if self.request.user.is_authenticated:
            if self.request.user.is_staff:
                # Se è staff/admin, mostra tutte le prenotazioni recenti
                context['prenotazioni_future'] = Prenotazione.objects.filter(
                    slot__data__gte=date.today(),
                    stato__in=['confermata', 'in_attesa']
                ).select_related('cliente', 'slot').order_by('slot__data', 'slot__ora_inizio')[:10]
                context['is_operator'] = True
            else:
                # Se è cliente, mostra solo le sue prenotazioni
                try:
                    cliente = self.request.user.cliente
                    context['prenotazioni_future'] = cliente.prenotazioni.filter(
                        slot__data__gte=date.today(),
                        stato__in=['confermata', 'in_attesa']
                    ).order_by('slot__data', 'slot__ora_inizio')[:5]
                except:
                    pass
                context['is_operator'] = False
        
        context['servizi'] = ServizioProdotto.objects.filter(tipo='servizio', attivo=True)
        return context


class CalendarioPrenotazioniView(TemplateView):
    """Calendario interattivo per prenotazioni"""
    template_name = 'prenotazioni/calendario.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Genera calendario per i prossimi 30 giorni
        oggi = date.today()
        giorni = []
        
        for i in range(30):
            data_giorno = oggi + timedelta(days=i)
            
            # Verifica se è un giorno speciale
            giorno_speciale = CalendarioPersonalizzato.objects.filter(data=data_giorno).first()
            
            if giorno_speciale and giorno_speciale.chiuso:
                giorni.append({
                    'data': data_giorno,
                    'disponibile': False,
                    'motivo': 'Chiuso'
                })
                continue
            
            # Conta slot disponibili
            slot_disponibili = SlotPrenotazione.objects.filter(
                data=data_giorno,
                disponibile=True
            ).annotate(
                posti_liberi=F('max_prenotazioni') - F('prenotazioni_attuali')
            ).filter(posti_liberi__gt=0).count()
            
            giorni.append({
                'data': data_giorno,
                'disponibile': slot_disponibili > 0,
                'slot_disponibili': slot_disponibili
            })
        
        context['calendario'] = giorni
        return context


class NuovaPrenotazioneView(TemplateView):
    """Vista wizard per creare nuova prenotazione guidata"""
    template_name = 'prenotazioni/prenotazione_guidata.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Aggiungi servizi disponibili per il template (categorie "Servito" e "Mezzi Pesanti")
        from apps.core.models import ServizioProdotto, Categoria
        categorie_prenotabili = Categoria.objects.filter(nome__in=['Servito', 'Mezzi Pesanti'])
        if categorie_prenotabili.exists():
            context['servizi_disponibili'] = ServizioProdotto.objects.filter(
                attivo=True,
                categoria__in=categorie_prenotabili
            ).select_related('categoria')
            context['categorie'] = categorie_prenotabili
        else:
            context['servizi_disponibili'] = ServizioProdotto.objects.filter(attivo=True).select_related('categoria')
            context['categorie'] = Categoria.objects.filter(attiva=True)
        
        # Aggiungi un form vuoto per il CSRF token
        from .forms import PrenotazioneForm
        context['form'] = PrenotazioneForm()
        
        # Cattura parametri dal calendario per precompilare data/ora
        data_param = self.request.GET.get('data')
        slot_param = self.request.GET.get('slot')
        ora_param = self.request.GET.get('ora')
        
        if data_param:
            context['data_preselezionata'] = data_param
        if slot_param:
            context['slot_preselezionato'] = slot_param
        if ora_param:
            context['ora_preselezionata'] = ora_param
        
        return context
    
    def post(self, request, *args, **kwargs):
        """Gestisce la creazione della prenotazione dal wizard"""
        print("=== WIZARD POST REQUEST ===")
        print(f"POST data: {dict(request.POST)}")
        
        try:
            # Recupera i dati dalla richiesta
            servizi_ids = request.POST.getlist('servizi_selezionati')
            servizi_ids = [int(sid) for sid in servizi_ids if str(sid).strip() and str(sid).isdigit()]
            
            data_prenotazione_str = request.POST.get('data_prenotazione', '').strip()
            ora_prenotazione_str = request.POST.get('ora_prenotazione', '').strip()
            durata_stimata = request.POST.get('durata_stimata_minuti', '0').strip()
            
            # Dati cliente
            cliente_id = request.POST.get('cliente', '').strip()
            nome_cliente = request.POST.get('nome_cliente', '').strip()
            cognome_cliente = request.POST.get('cognome_cliente', '').strip()
            telefono_cliente = request.POST.get('telefono_cliente', '').strip()
            tipo_auto = request.POST.get('tipo_auto', '').strip()
            note_cliente = request.POST.get('note_cliente', '').strip()
            
            print(f"Servizi: {servizi_ids}")
            print(f"Data: '{data_prenotazione_str}'")
            print(f"Ora: '{ora_prenotazione_str}'")
            print(f"Durata: '{durata_stimata}'")
            print(f"Cliente ID: '{cliente_id}'")
            print(f"Nome cliente: '{nome_cliente}'")
            print(f"Cognome cliente: '{cognome_cliente}'")
            print(f"Telefono: '{telefono_cliente}'")
            print(f"Tipo auto: '{tipo_auto}'")
            print(f"Note: '{note_cliente}'")
            
            # Validazione dati essenziali
            if not servizi_ids:
                messages.error(request, 'Devi selezionare almeno un servizio')
                return self.get(request, *args, **kwargs)
                
            if not data_prenotazione_str or not ora_prenotazione_str:
                messages.error(request, 'Devi selezionare data e ora')
                return self.get(request, *args, **kwargs)
                
            if not cliente_id and not nome_cliente:
                messages.error(request, 'Devi selezionare un cliente o inserire i dati per un nuovo cliente')
                return self.get(request, *args, **kwargs)
            
            # Crea la prenotazione
            prenotazione_id = self.crea_prenotazione(
                servizi_ids=servizi_ids,
                data_prenotazione=data_prenotazione_str,
                ora_prenotazione=ora_prenotazione_str,
                durata_stimata=durata_stimata,
                cliente_id=cliente_id,
                nome_cliente=nome_cliente,
                cognome_cliente=cognome_cliente,
                telefono_cliente=telefono_cliente,
                tipo_auto=tipo_auto,
                note_cliente=note_cliente
            )
            
            if prenotazione_id:
                return redirect('prenotazioni:dettaglio-prenotazione', pk=prenotazione_id)
            else:
                return self.get(request, *args, **kwargs)
                
        except Exception as e:
            print(f"Errore nel POST wizard: {str(e)}")
            import traceback
            traceback.print_exc()
            messages.error(request, f'Errore nella creazione della prenotazione: {str(e)}')
            return self.get(request, *args, **kwargs)
    
    def crea_prenotazione(self, servizi_ids, data_prenotazione, ora_prenotazione, durata_stimata, 
                         cliente_id, nome_cliente, cognome_cliente, telefono_cliente, tipo_auto, note_cliente):
        """Crea effettivamente la prenotazione con i dati forniti"""
        from .models import SlotPrenotazione, Prenotazione
        from apps.clienti.models import Cliente
        from apps.core.models import ServizioProdotto
        from django.db import IntegrityError, transaction
        from datetime import datetime, time
        import time as time_module
        
        try:
            with transaction.atomic():
                print("=== INIZIO CREAZIONE PRENOTAZIONE ===")
                
                # 1. Converte e valida i dati
                data_obj = datetime.strptime(data_prenotazione, '%Y-%m-%d').date()
                ora_obj = datetime.strptime(ora_prenotazione, '%H:%M').time()
                servizi = ServizioProdotto.objects.filter(id__in=servizi_ids)
                
                print(f"Data convertita: {data_obj}")
                print(f"Ora convertita: {ora_obj}")
                print(f"Servizi trovati: {servizi.count()}")
                
                if not servizi.exists():
                    messages.error(self.request, 'Servizi non trovati nel database')
                    return None
                
                # 2. Trova o crea lo slot
                slot, created = SlotPrenotazione.objects.get_or_create(
                    data=data_obj,
                    ora_inizio=ora_obj,
                    defaults={
                        'ora_fine': time(hour=(ora_obj.hour + 1) % 24, minute=ora_obj.minute),
                        'max_prenotazioni': 2,
                        'disponibile': True
                    }
                )
                print(f"Slot: {slot} (creato: {created})")
                
                # 3. Gestisce il cliente
                cliente = self.gestisci_cliente(cliente_id, nome_cliente, cognome_cliente, telefono_cliente, tipo_auto)
                if not cliente:
                    return None
                    
                print(f"Cliente: {cliente}")
                
                # 4. Calcola durata
                if durata_stimata and durata_stimata.isdigit() and int(durata_stimata) > 0:
                    durata_finale = int(durata_stimata)
                else:
                    durata_finale = sum(s.durata_minuti for s in servizi)
                
                print(f"Durata finale: {durata_finale} minuti")
                
                # 5. Crea la prenotazione
                prenotazione = Prenotazione.objects.create(
                    cliente=cliente,
                    slot=slot,
                    durata_stimata_minuti=durata_finale,
                    stato='confermata',
                    nota_cliente=note_cliente,
                    tipo_auto=tipo_auto
                )
                
                print(f"Prenotazione creata: ID={prenotazione.id}, Codice={prenotazione.codice_prenotazione}")
                
                # 6. Assegna i servizi
                prenotazione.servizi.set(servizi)
                print(f"Servizi assegnati: {[s.titolo for s in servizi]}")
                
                # 7. Forza aggiornamento contatori dopo assegnazione servizi
                if hasattr(prenotazione, 'slot') and prenotazione.slot:
                    prenotazione.slot.aggiorna_contatori()
                    print(f"Contatori slot aggiornati: {prenotazione.slot.prenotazioni_attuali}/{prenotazione.slot.max_prenotazioni}")
                
                messages.success(
                    self.request, 
                    f'Prenotazione creata con successo! Codice: {prenotazione.codice_prenotazione}'
                )
                
                return prenotazione.id
                
        except Exception as e:
            print(f"Errore nella creazione: {str(e)}")
            import traceback
            traceback.print_exc()
            messages.error(self.request, f'Errore nella creazione della prenotazione: {str(e)}')
            return None
    
    def gestisci_cliente(self, cliente_id, nome_cliente, cognome_cliente, telefono_cliente, tipo_auto):
        """Gestisce la selezione/creazione del cliente"""
        from apps.clienti.models import Cliente
        from django.db import IntegrityError
        import time as time_module
        
        try:
            if cliente_id and cliente_id.isdigit():
                # Cliente esistente
                cliente = Cliente.objects.get(id=cliente_id)
                print(f"Cliente esistente selezionato: {cliente.nome}")
                return cliente
                
            elif nome_cliente:
                # Nuovo cliente
                print(f"Creando nuovo cliente: {nome_cliente}")
                
                # Crea nuovo cliente con email temporanea
                email_finale = f"temp_{int(time_module.time())}@prenotazione.local"
                
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=nome_cliente,
                    cognome=cognome_cliente,
                    email=email_finale,
                    telefono=telefono_cliente or '',
                    consenso_marketing=False
                )
                
                print(f"Nuovo cliente creato: {cliente.nome_completo}")
                return cliente
                
            else:
                messages.error(self.request, 'Nessun cliente specificato')
                return None
                
        except Cliente.DoesNotExist:
            messages.error(self.request, f'Cliente con ID {cliente_id} non trovato')
            return None
        except IntegrityError as e:
            messages.error(self.request, f'Errore nell\'email del cliente: {str(e)}')
            return None
        except Exception as e:
            messages.error(self.request, f'Errore nella gestione del cliente: {str(e)}')
            return None


class NuovaPrenotazioneClassicaView(CreateView):
    """Versione classica della prenotazione (form tradizionale)"""
    model = Prenotazione
    form_class = PrenotazioneForm
    template_name = 'prenotazioni/prenotazione_form.html'
    
    def form_valid(self, form):
        from .models import SlotPrenotazione, Prenotazione
        
        # Recupera i dati dal form
        servizi = form.cleaned_data['servizi_selezionati']
        data_prenotazione = form.cleaned_data['data_prenotazione']
        ora_prenotazione = form.cleaned_data['ora_prenotazione']
        
        # Trova o crea lo slot
        slot, created = SlotPrenotazione.objects.get_or_create(
            data=data_prenotazione,
            ora_inizio=ora_prenotazione,
            defaults={
                'ora_fine': time(hour=(ora_prenotazione.hour + 1) % 24, minute=ora_prenotazione.minute),
                'max_prenotazioni': 2,
                'disponibile': True
            }
        )
        
        # Verifica disponibilità
        if not slot.is_disponibile:
            form.add_error(None, 'Slot non più disponibile')
            return super().form_invalid(form)
        
        # Crea la prenotazione
        prenotazione = Prenotazione()
        prenotazione.slot = slot
        prenotazione.durata_stimata_minuti = sum(s.durata_minuti for s in servizi)
        prenotazione.stato = 'confermata'
        
        # Gestione cliente
        cliente = form.cleaned_data.get('cliente')
        if cliente:
            prenotazione.cliente = cliente
        elif form.cleaned_data.get('nome_cliente'):
            # Crea un nuovo cliente con i dati forniti
            from apps.clienti.models import Cliente
            from django.db import IntegrityError
            
            # Gestione email opzionale
            email_cliente = form.cleaned_data.get('email_cliente', '')
            if email_cliente:
                try:
                    cliente_esistente = Cliente.objects.get(email=email_cliente)
                    prenotazione.cliente = cliente_esistente
                except Cliente.DoesNotExist:
                    # Crea nuovo cliente con email
                    try:
                        cliente = Cliente.objects.create(
                            tipo='privato',
                            nome=form.cleaned_data['nome_cliente'],
                            email=email_cliente,
                            telefono=form.cleaned_data.get('telefono_cliente', ''),
                            consenso_marketing=False
                        )
                        prenotazione.cliente = cliente
                    except IntegrityError:
                        form.add_error('email_cliente', 'Email già esistente')
                        return super().form_invalid(form)
            else:
                # Crea cliente senza email (uso un'email temporanea unica)
                import time as time_module
                email_temp = f"temp_{int(time_module.time())}@prenotazione.local"
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=form.cleaned_data['nome_cliente'],
                    email=email_temp,
                    telefono=form.cleaned_data.get('telefono_cliente', ''),
                    consenso_marketing=False
                )
                prenotazione.cliente = cliente
        elif self.request.user.is_authenticated:
            try:
                prenotazione.cliente = self.request.user.cliente
            except:
                # Se l'utente non ha un cliente associato, deve fornire i dati
                form.add_error(None, 'Devi fornire i dati del cliente o selezionare un cliente esistente')
                return super().form_invalid(form)
        else:
            # Se non è autenticato e non ha fornito dati cliente, errore
            form.add_error(None, 'Devi fornire i dati del cliente per completare la prenotazione')
            return super().form_invalid(form)
        
        # Verifica che ci sia un cliente assegnato
        if not prenotazione.cliente:
            form.add_error(None, 'Errore nella gestione del cliente')
            return super().form_invalid(form)
        
        # Salva per ottenere l'ID
        prenotazione.save()
        
        # Assegna i servizi
        prenotazione.servizi.set(servizi)
        
        # Forza aggiornamento contatori dopo assegnazione servizi
        if hasattr(prenotazione, 'slot') and prenotazione.slot:
            prenotazione.slot.aggiorna_contatori()
        
        self.object = prenotazione
        
        # Invia email di conferma (da implementare)
        messages.success(
            self.request,
            f'Prenotazione confermata! Codice: {prenotazione.codice_prenotazione}'
        )
        
        return redirect(self.get_success_url())
    
    def get_success_url(self):
        return reverse_lazy('prenotazioni:dettaglio-prenotazione', kwargs={'pk': self.object.pk})


class DettaglioPrenotazioneView(DetailView):
    model = Prenotazione
    template_name = 'prenotazioni/dettaglio_prenotazione.html'
    context_object_name = 'prenotazione'


@login_required
def annulla_prenotazione(request, pk):
    """Annulla una prenotazione"""
    prenotazione = get_object_or_404(Prenotazione, pk=pk)
    
    # Verifica che l'utente possa annullare
    if hasattr(request.user, 'cliente') and prenotazione.cliente == request.user.cliente:
        if prenotazione.can_be_cancelled:
            prenotazione.annulla('Annullata dal cliente')
            messages.success(request, 'Prenotazione annullata con successo')
        else:
            messages.error(request, 'Non è possibile annullare questa prenotazione')
    else:
        messages.error(request, 'Non autorizzato')
    
    return redirect('prenotazioni:prenotazioni')


def prenotazione_rapida_api(request):
    """API per creare prenotazione rapida"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non supportato'}, status=405)
    
    try:
        from .models import SlotPrenotazione, Prenotazione
        from apps.clienti.models import Cliente
        from apps.core.models import ServizioProdotto
        from django.db import IntegrityError
        import time as time_module
        
        # Recupera i dati dal POST
        servizio_id = request.POST.get('servizi_selezionati')
        data_prenotazione = request.POST.get('data_prenotazione')
        ora_prenotazione = request.POST.get('ora_prenotazione')
        
        if not all([servizio_id, data_prenotazione, ora_prenotazione]):
            return JsonResponse({'error': 'Dati mancanti'}, status=400)
        
        # Converte i dati
        try:
            data_prenotazione = datetime.strptime(data_prenotazione, '%Y-%m-%d').date()
            ora_prenotazione = datetime.strptime(ora_prenotazione, '%H:%M').time()
            servizio = ServizioProdotto.objects.get(id=servizio_id)
        except (ValueError, ServizioProdotto.DoesNotExist):
            return JsonResponse({'error': 'Dati non validi'}, status=400)
        
        # Trova o crea lo slot
        slot, created = SlotPrenotazione.objects.get_or_create(
            data=data_prenotazione,
            ora_inizio=ora_prenotazione,
            defaults={
                'ora_fine': time(hour=(ora_prenotazione.hour + 1) % 24, minute=ora_prenotazione.minute),
                'max_prenotazioni': 2,
                'disponibile': True
            }
        )
        
        # Verifica disponibilità
        if not slot.is_disponibile:
            return JsonResponse({'error': 'Slot non più disponibile'}, status=400)
        
        # Crea la prenotazione
        prenotazione = Prenotazione()
        prenotazione.slot = slot
        prenotazione.durata_stimata_minuti = servizio.durata_minuti
        prenotazione.stato = 'confermata'
        
        # Gestione cliente
        if request.user.is_authenticated:
            try:
                # Prova a trovare un cliente associato all'utente
                cliente_utente = Cliente.objects.get(email=request.user.email)
                prenotazione.cliente = cliente_utente
            except Cliente.DoesNotExist:
                # Se non esiste un cliente, usa i dati forniti o crea uno nuovo
                nome_completo = request.POST.get('nome_cliente') or request.user.get_full_name() or request.user.username
                # Dividi nome completo in nome e cognome
                parti_nome = nome_completo.split(' ', 1)
                nome_cliente = parti_nome[0]
                cognome_cliente = parti_nome[1] if len(parti_nome) > 1 else ''
                
                email_cliente = request.POST.get('email_cliente') or request.user.email
                telefono_cliente = request.POST.get('telefono_cliente', '')
                
                # Crea un nuovo cliente associato all'utente
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=nome_cliente,
                    cognome=cognome_cliente,
                    email=email_cliente,
                    telefono=telefono_cliente,
                    consenso_marketing=False
                )
                prenotazione.cliente = cliente
        else:
            # Crea cliente ospite
            nome_cliente = request.POST.get('nome_cliente', '')
            cognome_cliente = request.POST.get('cognome_cliente', '')
            email_cliente = request.POST.get('email_cliente', '')
            telefono_cliente = request.POST.get('telefono_cliente', '')
            
            if not nome_cliente or not cognome_cliente:
                return JsonResponse({'error': 'Nome e cognome cliente richiesti'}, status=400)
            
            # Verifica se esiste già un cliente con questa email
            if email_cliente:
                try:
                    cliente_esistente = Cliente.objects.get(email=email_cliente)
                    prenotazione.cliente = cliente_esistente
                except Cliente.DoesNotExist:
                    # Crea nuovo cliente
                    try:
                        cliente = Cliente.objects.create(
                            tipo='privato',
                            nome=nome_cliente,
                            cognome=cognome_cliente,
                            email=email_cliente,
                            telefono=telefono_cliente,
                            consenso_marketing=False
                        )
                        prenotazione.cliente = cliente
                    except IntegrityError:
                        return JsonResponse({'error': 'Email già esistente'}, status=400)
            else:
                # Crea cliente senza email
                email_temp = f"temp_{int(time_module.time())}@prenotazione.local"
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=nome_cliente,
                    cognome=cognome_cliente,
                    email=email_temp,
                    telefono=telefono_cliente,
                    consenso_marketing=False
                )
                prenotazione.cliente = cliente
        
        # Salva la prenotazione
        prenotazione.save()
        
        # Assegna i servizi
        prenotazione.servizi.add(servizio)
        
        # Forza aggiornamento contatori dopo assegnazione servizi
        if hasattr(prenotazione, 'slot') and prenotazione.slot:
            prenotazione.slot.aggiorna_contatori()
        
        return JsonResponse({
            'success': True,
            'prenotazione_id': prenotazione.id,
            'codice': prenotazione.codice_prenotazione,
            'redirect_url': reverse('prenotazioni:dettaglio-prenotazione', kwargs={'pk': prenotazione.pk})
        })
        
    except Exception as e:
        return JsonResponse({'error': f'Errore interno: {str(e)}'}, status=500)


def cerca_clienti_api(request):
    """API per cercare clienti"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Metodo non supportato'}, status=405)
    
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'clienti': []})
    
    from apps.clienti.models import Cliente
    from django.db.models import Q
    
    # Cerca clienti per nome, cognome, ragione sociale, email o telefono
    clienti = Cliente.objects.filter(
        Q(nome__icontains=query) |
        Q(cognome__icontains=query) |
        Q(ragione_sociale__icontains=query) |
        Q(email__icontains=query) |
        Q(telefono__icontains=query)
    ).order_by('cognome', 'ragione_sociale', 'nome')[:10]  # Ordine: cognome/ragione sociale, poi nome
    
    clienti_data = []
    for cliente in clienti:
        # Formato nome completo basato sul tipo cliente
        if cliente.tipo == 'privato':
            nome_completo = f"{cliente.nome} {cliente.cognome}".strip()
            tipo_display = "Privato"
        else:
            nome_completo = cliente.ragione_sociale or ""
            tipo_display = "Azienda"
        
        clienti_data.append({
            'id': cliente.id,
            'nome_completo': nome_completo,
            'tipo': cliente.tipo,
            'tipo_display': tipo_display,
            'telefono': cliente.telefono or '',
        })
    
    return JsonResponse({'clienti': clienti_data})


def slot_disponibili_api(request):
    """API per ottenere slot disponibili per una data"""
    data_str = request.GET.get('data')
    servizio_id = request.GET.get('servizio')
    
    if not data_str:
        return JsonResponse({'error': 'Data richiesta'}, status=400)
    
    try:
        data_richiesta = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Formato data non valido'}, status=400)
    
    # Genera slot per la data se non esistono
    giorno_settimana = data_richiesta.weekday()
    configurazioni = ConfigurazioneSlot.objects.filter(
        giorno_settimana=giorno_settimana,
        attivo=True
    )
    
    if servizio_id:
        configurazioni = configurazioni.filter(
            servizi_ammessi__id=servizio_id
        )
    
    # Crea slot se non esistono
    for config in configurazioni:
        config.genera_slot_per_data(data_richiesta)
    
    # Ottieni slot disponibili
    slot = SlotPrenotazione.objects.filter(
        data=data_richiesta,
        disponibile=True
    ).annotate(
        posti_liberi=F('max_prenotazioni') - F('prenotazioni_attuali')
    ).filter(posti_liberi__gt=0).order_by('ora_inizio')
    
    slot_data = []
    for s in slot:
        slot_data.append({
            'id': s.id,
            'ora_inizio': s.ora_inizio.strftime('%H:%M'),
            'ora_fine': s.ora_fine.strftime('%H:%M'),
            'posti_disponibili': s.posti_disponibili,
            'max_prenotazioni': s.max_prenotazioni
        })
    
    return JsonResponse({'slot': slot_data})


def calendario_mese_api(request):
    """API per ottenere dati del calendario per un mese"""
    try:
        year = int(request.GET.get('year', timezone.now().year))
        month = int(request.GET.get('month', timezone.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Anno o mese non validi'}, status=400)
    
    # Primo e ultimo giorno del mese
    primo_giorno = date(year, month, 1)
    if month == 12:
        ultimo_giorno = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_giorno = date(year, month + 1, 1) - timedelta(days=1)
    
    # Espandi per includere settimane complete
    giorni_da_includere = primo_giorno - timedelta(days=primo_giorno.weekday())
    fine_periodo = ultimo_giorno + timedelta(days=6-ultimo_giorno.weekday())
    
    calendario_data = {}
    
    # Itera su tutti i giorni del periodo
    giorno_corrente = giorni_da_includere
    while giorno_corrente <= fine_periodo:
        # Verifica giorni speciali
        giorno_speciale = CalendarioPersonalizzato.objects.filter(data=giorno_corrente).first()
        
        if giorno_speciale and giorno_speciale.chiuso:
            calendario_data[giorno_corrente.isoformat()] = {
                'data': giorno_corrente.isoformat(),
                'chiuso': True,
                'slot_disponibili': 0,
                'note': giorno_speciale.note
            }
        else:
            # Conta slot disponibili per questo giorno
            slot_count = SlotPrenotazione.objects.filter(
                data=giorno_corrente,
                disponibile=True
            ).annotate(
                posti_liberi=F('max_prenotazioni') - F('prenotazioni_attuali')
            ).filter(posti_liberi__gt=0).count()
            
            calendario_data[giorno_corrente.isoformat()] = {
                'data': giorno_corrente.isoformat(),
                'chiuso': False,
                'slot_disponibili': slot_count,
                'note': giorno_speciale.note if giorno_speciale else ''
            }
        
        giorno_corrente += timedelta(days=1)
    
    return JsonResponse(calendario_data)


def calendario_settimana_api(request):
    """API per ottenere dati del calendario per una settimana"""
    try:
        # Data di riferimento (default: oggi)
        data_str = request.GET.get('data', timezone.now().date().isoformat())
        data_riferimento = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Formato data non valido'}, status=400)
    
    # Calcola l'inizio della settimana (lunedì)
    inizio_settimana = data_riferimento - timedelta(days=data_riferimento.weekday())
    
    # Genera slot per tutta la settimana se non esistono
    for i in range(7):
        giorno = inizio_settimana + timedelta(days=i)
        giorno_settimana = giorno.weekday()
        
        # Trova le configurazioni per questo giorno
        configurazioni = ConfigurazioneSlot.objects.filter(
            giorno_settimana=giorno_settimana,
            attivo=True
        )
        
        # Crea slot se non esistono
        for config in configurazioni:
            config.genera_slot_per_data(giorno)
    
    # Costruisce la struttura dati per la settimana
    settimana_data = {
        'inizio_settimana': inizio_settimana.isoformat(),
        'giorni': []
    }
    
    giorni_nomi = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']
    
    for i in range(7):
        giorno = inizio_settimana + timedelta(days=i)
        
        # Verifica giorni speciali
        giorno_speciale = CalendarioPersonalizzato.objects.filter(data=giorno).first()
        
        # Ottieni tutti gli slot per questo giorno
        slot_giorno = SlotPrenotazione.objects.filter(
            data=giorno,
            disponibile=True
        ).order_by('ora_inizio')
        
        slot_data = []
        for slot in slot_giorno:
            # Ottieni prenotazioni per questo slot
            prenotazioni = slot.prenotazioni.filter(stato='confermata').select_related('cliente')
            
            slot_info = {
                'id': slot.id,
                'ora_inizio': slot.ora_inizio.strftime('%H:%M'),
                'ora_fine': slot.ora_fine.strftime('%H:%M'),
                'max_prenotazioni': slot.max_prenotazioni,
                'prenotazioni_attuali': slot.prenotazioni_attuali,
                'posti_disponibili': slot.posti_disponibili,
                'is_disponibile': slot.is_disponibile,
                'prenotazioni': []
            }
            
            # Aggiungi dettagli prenotazioni
            for prenotazione in prenotazioni:
                slot_info['prenotazioni'].append({
                    'id': prenotazione.id,
                    'codice': prenotazione.codice_prenotazione,
                    'cliente_nome': prenotazione.cliente.nome if prenotazione.cliente else 'Cliente Anonimo',
                    'tipo_auto': prenotazione.tipo_auto or '',
                    'servizi': [s.titolo for s in prenotazione.servizi.all()],
                    'nota': prenotazione.nota_cliente or '',
                    'durata_stimata': prenotazione.durata_stimata_minuti
                })
            
            slot_data.append(slot_info)
        
        giorno_info = {
            'data': giorno.isoformat(),
            'nome_giorno': giorni_nomi[i],
            'numero_giorno': giorno.day,
            'is_oggi': giorno == timezone.now().date(),
            'is_passato': giorno < timezone.now().date(),
            'chiuso': giorno_speciale.chiuso if giorno_speciale else False,
            'note': giorno_speciale.note if giorno_speciale else '',
            'slot': slot_data,
            'totale_slot': len(slot_data),
            'slot_disponibili': len([s for s in slot_data if s['posti_disponibili'] > 0])
        }
        
        settimana_data['giorni'].append(giorno_info)
    
    return JsonResponse(settimana_data)


def statistiche_calendario_api(request):
    """API per statistiche rapide del calendario"""
    oggi = timezone.now().date()
    
    # Slot disponibili oggi
    slot_oggi = SlotPrenotazione.objects.filter(
        data=oggi,
        disponibile=True
    ).annotate(
        posti_liberi=F('max_prenotazioni') - F('prenotazioni_attuali')
    ).filter(posti_liberi__gt=0).count()
    
    # Prossimo slot libero nei prossimi 7 giorni
    prossimo_libero = None
    for i in range(1, 8):
        data_futura = oggi + timedelta(days=i)
        slot_liberi = SlotPrenotazione.objects.filter(
            data=data_futura,
            disponibile=True
        ).annotate(
            posti_liberi=F('max_prenotazioni') - F('prenotazioni_attuali')
        ).filter(posti_liberi__gt=0).order_by('ora_inizio').first()
        
        if slot_liberi:
            giorni_nomi = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
            giorno_settimana = giorni_nomi[data_futura.weekday()]
            if i == 1:
                prossimo_libero = f"Domani {slot_liberi.ora_inizio.strftime('%H:%M')}"
            elif i == 2:
                prossimo_libero = f"Dopodomani {slot_liberi.ora_inizio.strftime('%H:%M')}"
            else:
                prossimo_libero = f"{giorno_settimana} {slot_liberi.ora_inizio.strftime('%H:%M')}"
            break
    
    return JsonResponse({
        'slot_oggi': slot_oggi,
        'prossimo_libero': prossimo_libero or 'N/A'
    })


@login_required
def duplica_configurazione_slot(request, pk):
    """Duplica una configurazione slot"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non supportato'}, status=405)
    
    try:
        slot_originale = get_object_or_404(ConfigurazioneSlot, pk=pk)
        
        # Crea una copia esatta (ora che non c'è più il vincolo unique)
        nuovo_slot = ConfigurazioneSlot.objects.create(
            giorno_settimana=slot_originale.giorno_settimana,
            ora_inizio=slot_originale.ora_inizio,
            ora_fine=slot_originale.ora_fine,
            durata_slot_minuti=slot_originale.durata_slot_minuti,
            max_prenotazioni_per_slot=slot_originale.max_prenotazioni_per_slot,
            attivo=slot_originale.attivo
        )
        
        # Copia i servizi ammessi
        nuovo_slot.servizi_ammessi.set(slot_originale.servizi_ammessi.all())
        
        return JsonResponse({
            'success': True,
            'message': f'Slot duplicato con successo per {nuovo_slot.get_giorno_settimana_display()} alle {nuovo_slot.ora_inizio.strftime("%H:%M")}'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def elimina_configurazione_slot(request, pk):
    """Elimina una configurazione slot"""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Metodo non supportato'}, status=405)
    
    try:
        slot = get_object_or_404(ConfigurazioneSlot, pk=pk)
        
        # Verifica se ci sono prenotazioni associate
        from django.db import models
        prenotazioni_future = SlotPrenotazione.objects.filter(
            data__gte=timezone.now().date(),
            ora_inizio=slot.ora_inizio,
            prenotazioni__isnull=False
        ).exists()
        
        if prenotazioni_future:
            return JsonResponse({
                'error': 'Impossibile eliminare: ci sono prenotazioni future associate a questo slot'
            }, status=400)
        
        slot_info = f"{slot.get_giorno_settimana_display()} {slot.ora_inizio.strftime('%H:%M')}"
        slot.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Slot {slot_info} eliminato con successo'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# Admin Prenotazioni
class PrenotazioniAdminListView(LoginRequiredMixin, ListView):
    model = Prenotazione
    template_name = 'prenotazioni/admin_prenotazioni_list.html'
    context_object_name = 'prenotazioni'
    paginate_by = 50
    
    def get_queryset(self):
        queryset = Prenotazione.objects.select_related('cliente', 'slot').prefetch_related('servizi')
        
        # Filtri
        stato = self.request.GET.get('stato')
        data = self.request.GET.get('data')
        
        if stato:
            queryset = queryset.filter(stato=stato)
        if data:
            queryset = queryset.filter(slot__data=data)
        else:
            # Default: prenotazioni di oggi e future
            queryset = queryset.filter(slot__data__gte=date.today())
        
        return queryset.order_by('slot__data', 'slot__ora_inizio')


class CheckinPrenotazioniView(LoginRequiredMixin, ListView):
    """Vista dedicata per la gestione del check-in delle prenotazioni"""
    model = Prenotazione
    template_name = 'prenotazioni/checkin_prenotazioni.html'
    context_object_name = 'prenotazioni'
    paginate_by = 20
    
    def get_queryset(self):
        from datetime import datetime, timedelta
        
        # Mostra prenotazioni per oggi e i prossimi giorni (confermate e in attesa)
        queryset = Prenotazione.objects.filter(
            stato__in=['confermata', 'in_attesa'],  
            slot__data__gte=date.today() - timedelta(days=1),  # Da ieri
            slot__data__lte=date.today() + timedelta(days=3)   # Fino a 3 giorni avanti
        ).select_related('cliente', 'slot').prefetch_related('servizi')
        
        # Filtri opzionali
        data_filtro = self.request.GET.get('data')
        cliente_filtro = self.request.GET.get('cliente')
        
        if data_filtro:
            try:
                data_obj = datetime.strptime(data_filtro, '%Y-%m-%d').date()
                queryset = queryset.filter(slot__data=data_obj)
            except ValueError:
                pass
                
        if cliente_filtro:
            queryset = queryset.filter(
                Q(cliente__nome__icontains=cliente_filtro) |
                Q(cliente__email__icontains=cliente_filtro)
            )
        
        # Debug: stampa informazioni sulla query
        final_queryset = queryset.order_by('slot__data', 'slot__ora_inizio')
        print(f"=== DEBUG CHECKIN PRENOTAZIONI ===")
        print(f"Data oggi: {date.today()}")
        print(f"Data domani: {date.today() + timedelta(days=1)}")
        print(f"Query SQL: {final_queryset.query}")
        print(f"Prenotazioni trovate: {final_queryset.count()}")
        
        # Mostra tutte le prenotazioni esistenti per debug
        tutte_prenotazioni = Prenotazione.objects.all()
        print(f"Totale prenotazioni nel DB: {tutte_prenotazioni.count()}")
        for p in tutte_prenotazioni[:5]:  # Mostra solo le prime 5
            print(f"  - ID: {p.id}, Stato: {p.stato}, Data: {p.slot.data if p.slot else 'N/A'}, Ordine: {'Sì' if p.ordine else 'No'}")
        
        return final_queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Statistiche per oggi
        oggi = date.today()
        prenotazioni_oggi = Prenotazione.objects.filter(
            slot__data=oggi,
            stato='confermata'
        )
        
        context['stats'] = {
            'totali_oggi': prenotazioni_oggi.count(),
            'da_fare_checkin': prenotazioni_oggi.filter(ordine__isnull=True).count(),
            'checkin_completati': prenotazioni_oggi.filter(ordine__isnull=False).count(),
        }
        
        # Filtri attivi
        context['filtri'] = {
            'data': self.request.GET.get('data', ''),
            'cliente': self.request.GET.get('cliente', ''),
        }
        
        # Prossimi slot per riferimento rapido
        from datetime import datetime, timedelta
        prossimi_slot = Prenotazione.objects.filter(
            stato='confermata',
            slot__data=oggi,
            slot__ora_inizio__gte=datetime.now().time(),
            ordine__isnull=True
        ).order_by('slot__ora_inizio')[:5]
        
        context['prossimi_slot'] = prossimi_slot
        
        # Variabili per i template
        context['today'] = oggi
        context['now_time'] = datetime.now().time()
        
        # Aggiungi clienti e servizi disponibili per il modal
        from apps.clienti.models import Cliente
        from apps.core.models import ServizioProdotto, Categoria
        
        context['clienti'] = Cliente.objects.all().order_by('nome')
        
        # Servizi disponibili per le prenotazioni (Servito e Mezzi Pesanti)
        categorie_prenotabili = Categoria.objects.filter(nome__in=['Servito', 'Mezzi Pesanti'])
        if categorie_prenotabili.exists():
            context['servizi_disponibili'] = ServizioProdotto.objects.filter(
                attivo=True,
                categoria__in=categorie_prenotabili
            ).select_related('categoria').order_by('categoria__nome', 'titolo')
        else:
            context['servizi_disponibili'] = ServizioProdotto.objects.filter(
                attivo=True,
                tipo='servizio'
            ).select_related('categoria').order_by('titolo')
        
        return context


@login_required
def checkin_prenotazione(request, pk):
    """Check-in di una prenotazione"""
    prenotazione = get_object_or_404(Prenotazione, pk=pk)
    
    # Controlla se l'utente è autorizzato (staff/admin)
    if not request.user.is_staff:
        messages.error(request, 'Non sei autorizzato ad effettuare il check-in')
        return redirect('prenotazioni:prenotazioni')
    
    if request.method == 'POST':
        try:
            # Verifica che la prenotazione sia confermata e non già convertita
            if prenotazione.stato != 'confermata':
                messages.error(request, 'La prenotazione deve essere confermata per effettuare il check-in')
                return redirect('prenotazioni:checkin-prenotazioni')
            
            if prenotazione.ordine:
                messages.warning(request, 'Check-in già effettuato per questa prenotazione')
                return redirect('prenotazioni:checkin-prenotazioni')
            
            # Recupera i dati modificabili dal form
            cliente_id = request.POST.get('cliente_id', '').strip()
            tipo_cliente = request.POST.get('tipo_cliente', '').strip()
            nome_cliente = request.POST.get('nome_cliente', '').strip()
            cognome_cliente = request.POST.get('cognome_cliente', '').strip()
            ragione_sociale = request.POST.get('ragione_sociale', '').strip()
            email_cliente = request.POST.get('email_cliente', '').strip()
            telefono_cliente = request.POST.get('telefono_cliente', '').strip()
            servizi_ids = request.POST.get('servizi_ids', '').strip()
            tipo_auto_modificato = request.POST.get('tipo_auto', '').strip()
            ora_consegna_richiesta = request.POST.get('ora_consegna_richiesta', '').strip()
            note_interne = request.POST.get('note_interne', '').strip()
            
            # Gestione cliente
            from apps.clienti.models import Cliente
            cliente_finale = None
            
            if cliente_id:
                # Cliente esistente selezionato
                try:
                    cliente_finale = Cliente.objects.get(id=cliente_id)
                except Cliente.DoesNotExist:
                    pass
            
            # Se non è stato selezionato un cliente esistente o dati sono stati modificati, crea/aggiorna
            if not cliente_finale or nome_cliente or ragione_sociale:
                if tipo_cliente == 'business' and ragione_sociale:
                    # Cliente business
                    cliente_finale, created = Cliente.objects.get_or_create(
                        ragione_sociale=ragione_sociale,
                        defaults={
                            'tipo_cliente': 'business',
                            'email': email_cliente,
                            'telefono': telefono_cliente
                        }
                    )
                    if not created and (email_cliente or telefono_cliente):
                        if email_cliente:
                            cliente_finale.email = email_cliente
                        if telefono_cliente:
                            cliente_finale.telefono = telefono_cliente
                        cliente_finale.save()
                        
                elif nome_cliente:
                    # Cliente privato
                    cliente_finale, created = Cliente.objects.get_or_create(
                        nome=nome_cliente,
                        cognome=cognome_cliente or '',
                        defaults={
                            'tipo_cliente': 'privato',
                            'email': email_cliente,
                            'telefono': telefono_cliente
                        }
                    )
                    if not created and (email_cliente or telefono_cliente):
                        if email_cliente:
                            cliente_finale.email = email_cliente
                        if telefono_cliente:
                            cliente_finale.telefono = telefono_cliente
                        cliente_finale.save()
            
            # Aggiorna il cliente nella prenotazione se è cambiato
            if cliente_finale and cliente_finale != prenotazione.cliente:
                prenotazione.cliente = cliente_finale
                prenotazione.save()
            
            # Gestione servizi
            if servizi_ids:
                from apps.core.models import ServizioProdotto
                servizi_id_list = [int(id.strip()) for id in servizi_ids.split(',') if id.strip().isdigit()]
                if servizi_id_list:
                    nuovi_servizi = ServizioProdotto.objects.filter(id__in=servizi_id_list)
                    prenotazione.servizi.set(nuovi_servizi)
                    # Ricalcola durata stimata
                    prenotazione.durata_stimata_minuti = sum(s.durata_minuti for s in nuovi_servizi)
                    prenotazione.save()
            
            # Aggiorna il tipo auto nella prenotazione se modificato
            if tipo_auto_modificato and tipo_auto_modificato != prenotazione.tipo_auto:
                prenotazione.tipo_auto = tipo_auto_modificato
                prenotazione.save()
            
            # Converte prenotazione in ordine
            ordine = prenotazione.converti_in_ordine(request.user)
            
            # Aggiorna l'ordine con i dati modificabili
            if ora_consegna_richiesta:
                from datetime import datetime
                try:
                    ora_obj = datetime.strptime(ora_consegna_richiesta, '%H:%M').time()
                    ordine.ora_consegna_richiesta = ora_obj
                except ValueError:
                    pass  # Ignora se il formato non è valido
            
            # Aggiungi note interne alle note dell'ordine
            if note_interne:
                if ordine.nota:
                    ordine.nota = f"{ordine.nota}\n\nNote Check-in: {note_interne}"
                else:
                    ordine.nota = f"Note Check-in: {note_interne}"
            
            ordine.save()
            
            # Aggiorna stato prenotazione
            prenotazione.stato = 'completata'
            prenotazione.save()
            
            messages.success(
                request,
                f'✅ Check-in completato! Ordine {ordine.numero_progressivo} creato per {prenotazione.cliente.nome}'
            )
            
            print(f"Check-in completato: Prenotazione {prenotazione.codice_prenotazione} -> Ordine {ordine.numero_progressivo}")
            
        except Exception as e:
            print(f"Errore nel check-in: {str(e)}")
            import traceback
            traceback.print_exc()
            messages.error(request, f'Errore nel check-in: {str(e)}')
    
    # Determina dove reindirizzare
    if 'from_checkin' in request.GET:
        return redirect('prenotazioni:checkin-prenotazioni')
    else:
        return redirect('prenotazioni:prenotazioni-admin')


# Customer Area Views (Area Cliente)
class AreaClienteDashboard(LoginRequiredMixin, TemplateView):
    template_name = 'clienti/area_cliente/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        try:
            cliente = self.request.user.cliente
            
            # Statistiche
            context['totale_ordini'] = cliente.get_ordini_totali()
            context['spesa_totale'] = cliente.get_spesa_totale()
            
            # Punti fedeltà
            from apps.clienti.models import PuntiFedelta
            punti_fedelta, created = PuntiFedelta.objects.get_or_create(cliente=cliente)
            context['punti_disponibili'] = punti_fedelta.punti_disponibili
            
            # Abbonamenti attivi
            context['abbonamenti_attivi'] = cliente.abbonamenti.filter(stato='attivo')
            
            # Prenotazioni future
            context['prenotazioni_future'] = cliente.prenotazioni.filter(
                slot__data__gte=date.today(),
                stato='confermata'
            ).order_by('slot__data', 'slot__ora_inizio')[:5]
            
            # Ultimi ordini
            context['ultimi_ordini'] = cliente.ordine_set.order_by('-data_ora')[:5]
            
        except AttributeError:
            # L'utente non ha un profilo cliente associato
            messages.error(self.request, 'Profilo cliente non trovato')
            context = {}
        
        return context


class ProfiloClienteView(LoginRequiredMixin, UpdateView):
    model = Cliente
    fields = [
        'nome', 'cognome', 'telefono', 'email',
        'indirizzo', 'cap', 'citta', 'consenso_marketing'
    ]
    template_name = 'clienti/area_cliente/profilo.html'
    success_url = reverse_lazy('clienti:area-cliente')
    
    def get_object(self):
        return self.request.user.cliente


class AbbonamentiClienteView(LoginRequiredMixin, ListView):
    model = Abbonamento
    template_name = 'clienti/area_cliente/abbonamenti.html'
    context_object_name = 'abbonamenti'
    
    def get_queryset(self):
        return self.request.user.cliente.abbonamenti.all().order_by('-data_attivazione')


class PuntiFedeltaClienteView(LoginRequiredMixin, TemplateView):
    template_name = 'clienti/area_cliente/punti_fedelta.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.request.user.cliente
        
        from apps.clienti.models import PuntiFedelta, MovimentoPunti
        punti_fedelta, created = PuntiFedelta.objects.get_or_create(cliente=cliente)
        
        context['punti_fedelta'] = punti_fedelta
        context['movimenti'] = MovimentoPunti.objects.filter(
            cliente=cliente
        ).order_by('-data_movimento')[:20]
        
        return context


class StatisticheClienteView(LoginRequiredMixin, TemplateView):
    template_name = 'clienti/area_cliente/statistiche.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.request.user.cliente
        
        # Statistiche ordini
        ordini = cliente.ordine_set.all()
        context['statistiche'] = {
            'ordini_totali': ordini.count(),
            'spesa_totale': sum(o.totale_finale for o in ordini),
            'spesa_media': sum(o.totale_finale for o in ordini) / max(ordini.count(), 1),
            'servizio_preferito': 'Lavaggio Completo',  # Da calcolare
            'ultimo_ordine': ordini.order_by('-data_ora').first(),
        }
        
        return context


class OrdiniClienteView(LoginRequiredMixin, ListView):
    model = Ordine
    template_name = 'clienti/area_cliente/ordini.html'
    context_object_name = 'ordini'
    paginate_by = 20
    
    def get_queryset(self):
        return self.request.user.cliente.ordine_set.order_by('-data_ora')


# CRUD Prenotazioni
class ModificaPrenotazioneView(LoginRequiredMixin, UpdateView):
    model = Prenotazione
    form_class = PrenotazioneForm
    template_name = 'prenotazioni/prenotazione_form.html'
    
    def get_initial(self):
        initial = super().get_initial()
        prenotazione = self.get_object()
        
        # Popola i campi del form con i dati della prenotazione esistente
        if prenotazione.slot:
            # Assicurati che la data sia nel formato corretto per input type="date" 
            initial['data_prenotazione'] = prenotazione.slot.data.isoformat() if prenotazione.slot.data else None
            initial['ora_prenotazione'] = prenotazione.slot.ora_inizio
        
        initial['cliente'] = prenotazione.cliente
        initial['servizi_selezionati'] = prenotazione.servizi.all()
        initial['durata_stimata_minuti'] = prenotazione.durata_stimata_minuti
        initial['note_cliente'] = prenotazione.nota_cliente
        initial['stato'] = prenotazione.stato
        initial['tipo_auto'] = prenotazione.tipo_auto
        
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['servizi_disponibili'] = ServizioProdotto.objects.filter(
            tipo='servizio', 
            attivo=True
        )
        context['clienti'] = Cliente.objects.all()
        return context
    
    def form_valid(self, form):
        try:
            # Ottieni la prenotazione esistente
            prenotazione = self.object
            
            # Estrai i dati dal form
            cliente = form.cleaned_data.get('cliente')
            nome_cliente = form.cleaned_data.get('nome_cliente')
            cognome_cliente = form.cleaned_data.get('cognome_cliente')
            telefono_cliente = form.cleaned_data.get('telefono_cliente')
            email_cliente = form.cleaned_data.get('email_cliente')
            tipo_auto = form.cleaned_data.get('tipo_auto')
            servizi_selezionati = form.cleaned_data.get('servizi_selezionati')
            data_prenotazione = form.cleaned_data.get('data_prenotazione')
            ora_prenotazione = form.cleaned_data.get('ora_prenotazione')
            durata_stimata_minuti = form.cleaned_data.get('durata_stimata_minuti')
            nota_cliente = form.cleaned_data.get('nota_cliente')
            nota_interna = form.cleaned_data.get('nota_interna')
            stato = form.cleaned_data.get('stato')
            
            from django.db import transaction
            
            with transaction.atomic():
                # 1. Gestisci il cliente
                if cliente:
                    prenotazione.cliente = cliente
                elif nome_cliente and cognome_cliente:
                    # Crea nuovo cliente temporaneo se necessario
                    from apps.clienti.models import Cliente
                    import time as time_module
                    
                    email_finale = email_cliente or f"temp_{int(time_module.time())}@prenotazione.local"
                    cliente_obj = Cliente.objects.create(
                        tipo='privato',
                        nome=nome_cliente,
                        cognome=cognome_cliente,
                        email=email_finale,
                        telefono=telefono_cliente or '',
                        consenso_marketing=False
                    )
                    prenotazione.cliente = cliente_obj
                
                # 2. Gestisci slot se data/ora è cambiata
                if data_prenotazione and ora_prenotazione:
                    slot_corrente = prenotazione.slot
                    
                    # Controlla se data/ora sono cambiate
                    if (slot_corrente.data != data_prenotazione or 
                        slot_corrente.ora_inizio != ora_prenotazione):
                        
                        from .models import SlotPrenotazione
                        from datetime import time
                        
                        # Trova o crea nuovo slot
                        if isinstance(ora_prenotazione, str):
                            ora_obj = time.fromisoformat(ora_prenotazione)
                        else:
                            ora_obj = ora_prenotazione
                            
                        nuovo_slot, created = SlotPrenotazione.objects.get_or_create(
                            data=data_prenotazione,
                            ora_inizio=ora_obj,
                            defaults={
                                'ora_fine': time(hour=(ora_obj.hour + 1) % 24, minute=ora_obj.minute),
                                'max_prenotazioni': 10,
                                'is_available': True
                            }
                        )
                        
                        # Aggiorna la prenotazione con il nuovo slot
                        prenotazione.slot = nuovo_slot
                        
                        # Aggiorna i contatori di entrambi gli slot
                        slot_corrente.aggiorna_contatori()
                        nuovo_slot.aggiorna_contatori()
                
                # 3. Aggiorna gli altri campi
                if tipo_auto is not None:
                    prenotazione.tipo_auto = tipo_auto
                if nota_cliente is not None:
                    prenotazione.nota_cliente = nota_cliente
                if nota_interna is not None:
                    prenotazione.nota_interna = nota_interna
                if stato:
                    prenotazione.stato = stato
                if durata_stimata_minuti:
                    prenotazione.durata_stimata_minuti = durata_stimata_minuti
                
                # 4. Salva la prenotazione
                prenotazione.save()
                
                # 5. Aggiorna i servizi
                if servizi_selezionati:
                    prenotazione.servizi.set(servizi_selezionati)
            
            messages.success(
                self.request,
                f'Prenotazione {prenotazione.codice_prenotazione} aggiornata con successo!'
            )
            return super().form_valid(form)
            
        except Exception as e:
            messages.error(self.request, f'Errore durante l\'aggiornamento: {str(e)}')
            return self.form_invalid(form)
    
    def get_success_url(self):
        return reverse_lazy('prenotazioni:dettaglio-prenotazione', kwargs={'pk': self.object.pk})


class EliminaPrenotazioneView(LoginRequiredMixin, DeleteView):
    model = Prenotazione
    template_name = 'prenotazioni/prenotazione_confirm_delete.html'
    success_url = reverse_lazy('prenotazioni:prenotazioni')
    
    def delete(self, request, *args, **kwargs):
        prenotazione = self.get_object()
        codice = prenotazione.codice_prenotazione
        
        # Log dell'eliminazione
        messages.success(
            request, 
            f'Prenotazione {codice} eliminata definitivamente.'
        )
        
        return super().delete(request, *args, **kwargs)


@login_required
@require_http_methods(["POST"])
def cancella_prenotazione(request, pk):
    """Vista AJAX per cancellare una prenotazione dalla vista admin"""
    try:
        prenotazione = get_object_or_404(Prenotazione, pk=pk)
        
        # Verifica che la prenotazione possa essere cancellata
        if prenotazione.stato not in ['in_attesa', 'confermata']:
            return JsonResponse({
                'success': False,
                'error': 'Impossibile cancellare una prenotazione già completata o annullata.'
            })
        
        # Annulla la prenotazione
        motivo = "Cancellata dall'operatore"
        success = prenotazione.annulla(motivo)
        
        if success:
            return JsonResponse({
                'success': True,
                'message': f'Prenotazione {prenotazione.codice_prenotazione} cancellata con successo.'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Impossibile cancellare la prenotazione.'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Errore durante la cancellazione: {str(e)}'
        })