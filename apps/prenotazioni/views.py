from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
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
        
        # Aggiungi servizi disponibili per il template
        from apps.core.models import ServizioProdotto
        context['servizi_disponibili'] = ServizioProdotto.objects.filter(attivo=True)
        
        # Aggiungi un form vuoto per il CSRF token
        from .forms import PrenotazioneForm
        context['form'] = PrenotazioneForm()
        
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
            telefono_cliente = request.POST.get('telefono_cliente', '').strip()
            tipo_auto = request.POST.get('tipo_auto', '').strip()
            note_cliente = request.POST.get('note_cliente', '').strip()
            
            print(f"Servizi: {servizi_ids}")
            print(f"Data: '{data_prenotazione_str}'")
            print(f"Ora: '{ora_prenotazione_str}'")
            print(f"Durata: '{durata_stimata}'")
            print(f"Cliente ID: '{cliente_id}'")
            print(f"Nome cliente: '{nome_cliente}'")
            print(f"Telefono: '{telefono_cliente}'")
            print(f"Tipo auto: '{tipo_auto}'")
            
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
                         cliente_id, nome_cliente, telefono_cliente, tipo_auto, note_cliente):
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
                cliente = self.gestisci_cliente(cliente_id, nome_cliente, telefono_cliente, tipo_auto)
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
    
    def gestisci_cliente(self, cliente_id, nome_cliente, telefono_cliente, tipo_auto):
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
                    email=email_finale,
                    telefono=telefono_cliente or '',
                    consenso_marketing=False
                )
                
                print(f"Nuovo cliente creato: {cliente.nome}")
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
        
        # Aggiorna contatori slot
        slot.aggiorna_contatori()
        
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
                nome_cliente = request.POST.get('nome_cliente') or request.user.get_full_name() or request.user.username
                email_cliente = request.POST.get('email_cliente') or request.user.email
                telefono_cliente = request.POST.get('telefono_cliente', '')
                
                # Crea un nuovo cliente associato all'utente
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=nome_cliente,
                    email=email_cliente,
                    telefono=telefono_cliente,
                    consenso_marketing=False
                )
                prenotazione.cliente = cliente
        else:
            # Crea cliente ospite
            nome_cliente = request.POST.get('nome_cliente', '')
            email_cliente = request.POST.get('email_cliente', '')
            telefono_cliente = request.POST.get('telefono_cliente', '')
            
            if not nome_cliente:
                return JsonResponse({'error': 'Nome cliente richiesto'}, status=400)
            
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
                    email=email_temp,
                    telefono=telefono_cliente,
                    consenso_marketing=False
                )
                prenotazione.cliente = cliente
        
        # Salva la prenotazione
        prenotazione.save()
        
        # Assegna i servizi
        prenotazione.servizi.add(servizio)
        
        # Aggiorna contatori slot
        slot.aggiorna_contatori()
        
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
    
    # Cerca clienti per nome, email o telefono
    clienti = Cliente.objects.filter(
        Q(nome__icontains=query) |
        Q(email__icontains=query) |
        Q(telefono__icontains=query)
    ).order_by('nome')[:10]  # Limita a 10 risultati
    
    clienti_data = []
    for cliente in clienti:
        clienti_data.append({
            'id': cliente.id,
            'nome': cliente.nome,
            'email': cliente.email,
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
        
        # Mostra solo prenotazioni confermate per oggi e i prossimi giorni
        queryset = Prenotazione.objects.filter(
            stato='confermata',
            slot__data__gte=date.today(),
            slot__data__lte=date.today() + timedelta(days=1),  # Oggi e domani
            ordine__isnull=True  # Non ancora convertite in ordini
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
        
        return queryset.order_by('slot__data', 'slot__ora_inizio')
    
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
            
            # Converte prenotazione in ordine
            ordine = prenotazione.converti_in_ordine(request.user)
            
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
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['servizi_disponibili'] = ServizioProdotto.objects.filter(
            tipo='servizio', 
            attivo=True
        )
        context['clienti'] = Cliente.objects.filter(attivo=True)
        return context
    
    def form_valid(self, form):
        messages.success(
            self.request,
            f'Prenotazione {form.instance.codice_prenotazione} aggiornata con successo!'
        )
        return super().form_valid(form)
    
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