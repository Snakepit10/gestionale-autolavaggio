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
from django.utils import timezone
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
import json
from datetime import timedelta
from .models import Ordine, ItemOrdine, Pagamento
from .forms import OrdineForm, PagamentoForm
from apps.clienti.models import Cliente
from apps.clienti.forms import ClienteQuickForm
from apps.core.models import ServizioProdotto, Sconto, Categoria
from .services import CalcoloTempoAttesaService, StampaService


class CassaView(LoginRequiredMixin, TemplateView):
    """Interfaccia principale punto cassa"""
    template_name = 'ordini/cassa.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Categorie e servizi/prodotti
        context['categorie'] = Categoria.objects.filter(attiva=True)
        context['servizi_prodotti'] = ServizioProdotto.objects.filter(attivo=True).select_related('categoria')
        
        # Sconti disponibili
        context['sconti'] = Sconto.objects.filter(attivo=True)
        
        # Form cliente veloce
        context['cliente_form'] = ClienteQuickForm()
        
        # Ordini recenti
        context['ordini_recenti'] = Ordine.objects.filter(
            operatore=self.request.user
        ).order_by('-data_ora')[:10]
        
        return context


class CassaMobileView(LoginRequiredMixin, TemplateView):
    """Interfaccia cassa ottimizzata per mobile (PWA)"""
    template_name = 'ordini/cassa_mobile.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Dati ottimizzati per mobile
        context['categorie'] = Categoria.objects.filter(attiva=True)
        context['servizi_frequenti'] = ServizioProdotto.objects.filter(
            attivo=True, tipo='servizio'
        ).order_by('-id')[:12]  # Ultimi 12 servizi più utilizzati
        
        return context


@login_required
def aggiungi_al_carrello(request):
    """AJAX: Aggiunge un item al carrello di sessione"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        data = json.loads(request.body)
        servizio_prodotto_id = data.get('servizio_prodotto_id')
        quantita = int(data.get('quantita', 1))
        
        servizio_prodotto = get_object_or_404(ServizioProdotto, id=servizio_prodotto_id)
        
        # Verifica disponibilità per prodotti
        if servizio_prodotto.tipo == 'prodotto' and servizio_prodotto.quantita_disponibile > 0:
            if servizio_prodotto.quantita_disponibile < quantita:
                return JsonResponse({
                    'error': f'Disponibili solo {servizio_prodotto.quantita_disponibile} unità'
                }, status=400)
        
        # Gestisci carrello in sessione
        carrello = request.session.get('carrello', {})
        item_key = str(servizio_prodotto_id)
        
        if item_key in carrello:
            carrello[item_key]['quantita'] += quantita
        else:
            carrello[item_key] = {
                'id': servizio_prodotto.id,
                'titolo': servizio_prodotto.titolo,
                'prezzo': float(servizio_prodotto.prezzo),
                'quantita': quantita,
                'tipo': servizio_prodotto.tipo
            }
        
        request.session['carrello'] = carrello
        request.session.modified = True
        
        # Calcola totali
        totale_items = sum(item['quantita'] for item in carrello.values())
        totale_prezzo = sum(item['prezzo'] * item['quantita'] for item in carrello.values())
        
        return JsonResponse({
            'success': True,
            'carrello': carrello,
            'totale_items': totale_items,
            'totale_prezzo': totale_prezzo
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def rimuovi_dal_carrello(request):
    """AJAX: Rimuove un item dal carrello"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        data = json.loads(request.body)
        servizio_prodotto_id = str(data.get('servizio_prodotto_id'))
        
        carrello = request.session.get('carrello', {})
        
        if servizio_prodotto_id == 'all':
            # Svuota completamente il carrello
            carrello = {}
            request.session['carrello'] = carrello
            
            # Pulisce anche lo sconto applicato
            if 'sconto_applicato' in request.session:
                del request.session['sconto_applicato']
            
            request.session.modified = True
        elif servizio_prodotto_id in carrello:
            del carrello[servizio_prodotto_id]
            request.session['carrello'] = carrello
            request.session.modified = True
        
        # Calcola totali
        totale_items = sum(item['quantita'] for item in carrello.values())
        totale_prezzo = sum(item['prezzo'] * item['quantita'] for item in carrello.values())
        
        return JsonResponse({
            'success': True,
            'carrello': carrello,
            'totale_items': totale_items,
            'totale_prezzo': totale_prezzo
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def stato_carrello(request):
    """AJAX: Restituisce lo stato attuale del carrello dalla sessione"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        carrello = request.session.get('carrello', {})
        sconto_applicato = request.session.get('sconto_applicato')
        
        # Calcola totali
        totale_items = sum(item['quantita'] for item in carrello.values())
        totale_prezzo = sum(item['prezzo'] * item['quantita'] for item in carrello.values())
        
        return JsonResponse({
            'success': True,
            'carrello': carrello,
            'sconto_applicato': sconto_applicato,
            'totale_items': totale_items,
            'totale_prezzo': totale_prezzo
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def applica_sconto(request):
    """AJAX: Applica uno sconto al carrello"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        data = json.loads(request.body)
        sconto_id = data.get('sconto_id')
        
        if sconto_id:
            sconto = get_object_or_404(Sconto, id=sconto_id, attivo=True)
            request.session['sconto_applicato'] = {
                'id': sconto.id,
                'titolo': sconto.titolo,
                'tipo': sconto.tipo_sconto,
                'valore': float(sconto.valore)
            }
        else:
            # Rimuovi sconto
            if 'sconto_applicato' in request.session:
                del request.session['sconto_applicato']
        
        request.session.modified = True
        
        # Calcola totali con sconto
        carrello = request.session.get('carrello', {})
        totale_prezzo = sum(item['prezzo'] * item['quantita'] for item in carrello.values())
        
        sconto_applicato = request.session.get('sconto_applicato')
        importo_sconto = 0
        
        if sconto_applicato:
            if sconto_applicato['tipo'] == 'percentuale':
                importo_sconto = totale_prezzo * (sconto_applicato['valore'] / 100)
            else:
                importo_sconto = min(sconto_applicato['valore'], totale_prezzo)
        
        totale_finale = totale_prezzo - importo_sconto
        
        return JsonResponse({
            'success': True,
            'totale_prezzo': totale_prezzo,
            'importo_sconto': importo_sconto,
            'totale_finale': totale_finale,
            'sconto_applicato': sconto_applicato
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def calcola_tempo_attesa(request):
    """AJAX: Calcola il tempo di attesa per l'ordine corrente"""
    try:
        carrello = request.session.get('carrello', {})
        if not carrello:
            return JsonResponse({'tempo_attesa_minuti': 0, 'ora_consegna_prevista': None})
        
        # Ottieni i servizi dal carrello
        servizi_ids = [int(item['id']) for item in carrello.values() if item.get('tipo') == 'servizio']
        servizi = ServizioProdotto.objects.filter(id__in=servizi_ids, tipo='servizio')
        
        # Calcola tempo di attesa
        risultato = CalcoloTempoAttesaService.calcola_tempo_attesa_nuovo_ordine(servizi)
        
        return JsonResponse(risultato)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@transaction.atomic
def completa_ordine(request):
    """Completa l'ordine e gestisce il pagamento"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        data = json.loads(request.body)
        
        # Valida carrello
        carrello = request.session.get('carrello', {})
        if not carrello:
            return JsonResponse({'error': 'Carrello vuoto'}, status=400)
        
        # Cliente (opzionale)
        cliente_id = data.get('cliente_id')
        cliente = None
        if cliente_id:
            cliente = get_object_or_404(Cliente, id=cliente_id)
        
        # Crea nuovo cliente se richiesto
        if data.get('nuovo_cliente'):
            cliente_form = ClienteQuickForm(data.get('nuovo_cliente'))
            if cliente_form.is_valid():
                cliente = cliente_form.save()
            else:
                return JsonResponse({
                    'error': 'Dati cliente non validi',
                    'form_errors': cliente_form.errors
                }, status=400)
        
        # Calcola totali
        totale_prezzo = sum(item['prezzo'] * item['quantita'] for item in carrello.values())
        
        # Applica sconto
        sconto_applicato = request.session.get('sconto_applicato')
        sconto_obj = None
        importo_sconto = 0
        
        if sconto_applicato:
            sconto_obj = Sconto.objects.get(id=sconto_applicato['id'])
            importo_sconto = sconto_obj.calcola_sconto(totale_prezzo)
        
        totale_finale = totale_prezzo - importo_sconto
        
        # Crea ordine
        ordine = Ordine.objects.create(
            cliente=cliente,
            origine='operatore',
            tipo_consegna=data.get('tipo_consegna', 'immediata'),
            ora_consegna_richiesta=data.get('ora_consegna_richiesta'),
            totale=totale_prezzo,
            sconto_applicato=sconto_obj,
            importo_sconto=importo_sconto,
            totale_finale=totale_finale,
            metodo_pagamento=data.get('metodo_pagamento', 'contanti'),
            nota=data.get('nota', ''),
            tipo_auto=data.get('tipo_auto', ''),
            operatore=request.user
        )
        
        # Calcola tempo di attesa
        servizi_nel_carrello = []
        for item in carrello.values():
            if item.get('tipo') == 'servizio':
                servizio = ServizioProdotto.objects.get(id=item['id'])
                servizi_nel_carrello.append(servizio)
        
        if servizi_nel_carrello:
            tempo_attesa = CalcoloTempoAttesaService.calcola_tempo_attesa_nuovo_ordine(servizi_nel_carrello)
            ordine.tempo_attesa_minuti = tempo_attesa['tempo_attesa_minuti']
            ordine.ora_consegna_prevista = tempo_attesa['ora_consegna_prevista']
            ordine.save()
        
        # Crea items ordine
        for item in carrello.values():
            servizio_prodotto = ServizioProdotto.objects.get(id=item['id'])
            
            ItemOrdine.objects.create(
                ordine=ordine,
                servizio_prodotto=servizio_prodotto,
                quantita=item['quantita'],
                prezzo_unitario=item['prezzo']
            )
        
        # Gestisci pagamento solo se esplicitamente richiesto
        importo_pagamento_str = data.get('importo_pagamento', '')
        metodo_pagamento = data.get('metodo_pagamento', 'contanti')
        
        # DEBUG: Aggiungi log per diagnosticare il problema
        print(f"DEBUG: importo_pagamento_str = '{importo_pagamento_str}'")
        print(f"DEBUG: metodo_pagamento = '{metodo_pagamento}'")
        print(f"DEBUG: data completa = {data}")
        
        # Registra pagamento solo se l'utente ha inserito un importo specifico
        if importo_pagamento_str and importo_pagamento_str.strip():
            try:
                importo_pagamento = float(importo_pagamento_str)
                print(f"DEBUG: importo_pagamento convertito = {importo_pagamento}")
                if importo_pagamento > 0:
                    # Converti a Decimal per evitare errori di tipo
                    from decimal import Decimal
                    importo_decimal = Decimal(str(importo_pagamento))
                    print(f"DEBUG: importo_decimal = {importo_decimal}")
                    
                    pagamento = Pagamento.objects.create(
                        ordine=ordine,
                        importo=importo_decimal,
                        metodo=metodo_pagamento,
                        operatore=request.user
                    )
                    print(f"DEBUG: Pagamento creato = {pagamento}")
                else:
                    print("DEBUG: Importo pagamento <= 0")
            except Exception as e:
                print(f"DEBUG: Errore durante creazione pagamento: {e}")
                print(f"DEBUG: Tipo errore: {type(e)}")
                # Se l'importo non è valido, non registrare il pagamento
                pass
        else:
            print("DEBUG: importo_pagamento_str vuoto o non valido")
        
        # Pulisci sessione
        if 'carrello' in request.session:
            del request.session['carrello']
        if 'sconto_applicato' in request.session:
            del request.session['sconto_applicato']
        request.session.modified = True
        
        # Stampa scontrino se richiesto
        if data.get('stampa_scontrino', False):  # Cambiato default a False
            try:
                StampaService.stampa_scontrino(ordine)
            except Exception as e:
                # Non bloccare l'ordine per errori di stampa
                print(f'Errore stampa scontrino: {str(e)}')  # Log invece di messages
        
        # Invia notifica WebSocket alle postazioni
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            if channel_layer:
                for item in ordine.items.all():
                    if item.postazione_assegnata:
                        async_to_sync(channel_layer.group_send)(
                            f'postazione_{item.postazione_assegnata.id}',
                            {
                                'type': 'nuovo_ordine',
                                'ordine': {
                                    'id': ordine.id,
                                    'numero_progressivo': ordine.numero_progressivo,
                                    'cliente': str(ordine.cliente) if ordine.cliente else 'Anonimo',
                                    'items': [{
                                        'servizio': item.servizio_prodotto.titolo,
                                        'quantita': item.quantita
                                    } for item in ordine.items.filter(postazione_assegnata=item.postazione_assegnata)]
                                }
                            }
                        )
        except Exception as e:
            print(f'Errore invio notifica WebSocket: {str(e)}')
        
        # Calcola resto solo se c'è stato un pagamento
        resto = 0
        if importo_pagamento_str and importo_pagamento_str.strip():
            try:
                from decimal import Decimal
                importo_pagamento = Decimal(importo_pagamento_str)
                # Converti totale_finale a Decimal per essere sicuri
                totale_finale_decimal = Decimal(str(ordine.totale_finale))
                print(f"DEBUG: Calcolo resto - importo_pagamento = {importo_pagamento}")
                print(f"DEBUG: Calcolo resto - totale_finale_decimal = {totale_finale_decimal}")
                differenza = importo_pagamento - totale_finale_decimal
                print(f"DEBUG: Calcolo resto - differenza = {differenza}")
                resto = max(0, float(differenza))
                print(f"DEBUG: Calcolo resto - resto finale = {resto}")
            except Exception as e:
                print(f"DEBUG: Errore calcolo resto: {e}")
                print(f"DEBUG: Tipo errore resto: {type(e)}")
                resto = 0
        
        # Aggiungi importo pagamento per il messaggio di risposta
        importo_pagamento_effettivo = 0
        if importo_pagamento_str and importo_pagamento_str.strip():
            try:
                from decimal import Decimal
                importo_pagamento_effettivo = float(importo_pagamento_str)
            except (ValueError, TypeError):
                importo_pagamento_effettivo = 0
        
        return JsonResponse({
            'success': True,
            'ordine_id': ordine.id,
            'numero_progressivo': ordine.numero_progressivo,
            'totale_finale': float(ordine.totale_finale),
            'importo_pagamento': importo_pagamento_effettivo,
            'resto': resto
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


class OrdiniListView(LoginRequiredMixin, ListView):
    model = Ordine
    template_name = 'ordini/ordini_list.html'
    context_object_name = 'ordini'
    paginate_by = 50
    
    def get_queryset(self):
        queryset = Ordine.objects.select_related('cliente', 'operatore').prefetch_related('items', 'pagamenti')
        
        # Filtri
        stato = self.request.GET.get('stato')
        data_da = self.request.GET.get('data_da')
        data_a = self.request.GET.get('data_a')
        cliente = self.request.GET.get('cliente')
        
        if stato:
            queryset = queryset.filter(stato=stato)
        if data_da:
            queryset = queryset.filter(data_ora__date__gte=data_da)
        if data_a:
            queryset = queryset.filter(data_ora__date__lte=data_a)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        
        return queryset.order_by('-data_ora')


class OrdineDetailView(LoginRequiredMixin, DetailView):
    model = Ordine
    template_name = 'ordini/ordine_detail.html'
    context_object_name = 'ordine'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Items raggruppati per postazione
        context['items_per_postazione'] = self.object.get_items_per_postazione()
        
        # Pagamenti
        context['pagamenti'] = self.object.pagamenti.all()
        
        # Form per nuovo pagamento
        context['pagamento_form'] = PagamentoForm()
        
        return context


@login_required
def registra_pagamento(request, pk):
    """Registra un nuovo pagamento per un ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    if request.method == 'POST':
        # Gestisci sia richieste JSON che form normali
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
                importo = float(data.get('importo', 0))
                metodo = data.get('metodo_pagamento', 'contanti')
                note = data.get('note', '')
                
                if importo <= 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'Importo non valido'
                    })
                
                # Crea pagamento
                pagamento = Pagamento.objects.create(
                    ordine=ordine,
                    importo=importo,
                    metodo=metodo,
                    nota=note,
                    operatore=request.user
                )
                
                return JsonResponse({
                    'success': True,
                    'message': f'Pagamento di €{pagamento.importo} registrato',
                    'ordine_completamente_pagato': ordine.is_pagato
                })
                
            except (json.JSONDecodeError, ValueError) as e:
                return JsonResponse({
                    'success': False,
                    'error': 'Dati non validi'
                })
        else:
            # Gestione form normale
            form = PagamentoForm(request.POST)
            if form.is_valid():
                pagamento = form.save(commit=False)
                pagamento.ordine = ordine
                pagamento.operatore = request.user
                pagamento.save()
                
                messages.success(request, f'Pagamento di €{pagamento.importo} registrato')
                
                # Controlla se ordine è completamente pagato
                if ordine.is_pagato:
                    messages.success(request, 'Ordine completamente saldato')
            else:
                messages.error(request, 'Errore nei dati del pagamento')
    
    return redirect('ordini:ordine-detail', pk=pk)


@login_required
def dettaglio_ordine_json(request, pk):
    """Restituisce i dettagli di un ordine in formato JSON"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    return JsonResponse({
        'id': ordine.id,
        'numero_progressivo': ordine.numero_progressivo,
        'totale_finale': float(ordine.totale_finale),
        'importo_pagato': float(ordine.importo_pagato),
        'saldo_dovuto': float(ordine.saldo_dovuto),
        'stato_pagamento': ordine.stato_pagamento,
        'stato_pagamento_display': ordine.get_stato_pagamento_display(),
        'cliente': ordine.cliente.nome_completo if ordine.cliente else 'Cliente anonimo',
        'data_ora': ordine.data_ora.strftime('%d/%m/%Y %H:%M')
    })


class OrdiniNonPagatiView(LoginRequiredMixin, ListView):
    model = Ordine
    template_name = 'ordini/ordini_non_pagati.html'
    context_object_name = 'ordini'
    
    def get_queryset(self):
        return Ordine.objects.filter(
            stato_pagamento__in=['non_pagato', 'parziale']
        ).select_related('cliente').order_by('-data_ora')


@login_required
def stampa_ordine(request, pk):
    """Stampa ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    try:
        # TODO: Implementare stampa ordine
        messages.info(request, 'Funzione stampa ordine non ancora implementata')
    except Exception as e:
        messages.error(request, f'Errore durante la stampa: {str(e)}')
    
    return redirect('ordini:ordine-detail', pk=pk)


@login_required
def stampa_scontrino(request, numero):
    """Stampa scontrino per un ordine"""
    ordine = get_object_or_404(Ordine, numero_progressivo=numero)
    
    try:
        StampaService.stampa_scontrino(ordine)
        messages.success(request, 'Scontrino stampato con successo')
    except Exception as e:
        messages.error(request, f'Errore durante la stampa: {str(e)}')
    
    return redirect('ordini:ordine-detail', pk=ordine.pk)


@login_required
def cambia_stato_ordine(request, pk):
    """Cambia lo stato di un ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            nuovo_stato = data.get('stato')
            
            # Valida che lo stato sia valido
            stati_validi = [choice[0] for choice in Ordine.STATO_CHOICES]
            if nuovo_stato not in stati_validi:
                return JsonResponse({
                    'success': False,
                    'error': 'Stato non valido'
                })
            
            # Valida transizioni di stato
            if not _valida_transizione_stato(ordine.stato, nuovo_stato):
                return JsonResponse({
                    'success': False,
                    'error': 'Transizione di stato non consentita'
                })
            
            # Cambia lo stato
            vecchio_stato = ordine.stato
            ordine.stato = nuovo_stato
            ordine.save()
            
            # Log dell'operazione
            print(f"Ordine {ordine.numero_progressivo} cambiato da {vecchio_stato} a {nuovo_stato} da {request.user}")
            
            # Notifica WebSocket alle dashboard delle postazioni e lista ordini
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                
                channel_layer = get_channel_layer()
                if channel_layer:
                    # Notifica tutte le postazioni che hanno items di questo ordine
                    postazioni_coinvolte = set()
                    for item in ordine.items.all():
                        if item.postazione_assegnata:
                            postazioni_coinvolte.add(item.postazione_assegnata.id)
                    
                    # Invia notifica a ogni postazione coinvolta
                    for postazione_id in postazioni_coinvolte:
                        async_to_sync(channel_layer.group_send)(
                            f'postazione_{postazione_id}',
                            {
                                'type': 'order_status_update',
                                'ordine_id': ordine.id,
                                'numero_progressivo': ordine.numero_progressivo,
                                'vecchio_stato': vecchio_stato,
                                'nuovo_stato': nuovo_stato,
                                'timestamp': timezone.now().isoformat()
                            }
                        )
                    
                    # Notifica anche la lista ordini generale
                    async_to_sync(channel_layer.group_send)(
                        'ordini_list',
                        {
                            'type': 'order_status_update',
                            'ordine_id': ordine.id,
                            'numero_progressivo': ordine.numero_progressivo,
                            'vecchio_stato': vecchio_stato,
                            'nuovo_stato': nuovo_stato,
                            'stato_display': ordine.get_stato_display(),
                            'timestamp': timezone.now().isoformat()
                        }
                    )
            except ImportError:
                # Django Channels non installato, ignora
                pass
            except Exception as e:
                # Errore WebSocket, ma non bloccare l'aggiornamento
                print(f'Errore WebSocket: {e}')
            
            return JsonResponse({
                'success': True,
                'message': f'Stato cambiato a {ordine.get_stato_display()}'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    })


def _valida_transizione_stato(stato_attuale, nuovo_stato):
    """Valida se una transizione di stato è consentita"""
    transizioni_consentite = {
        'in_attesa': ['in_lavorazione', 'annullato'],
        'in_lavorazione': ['completato', 'annullato'],
        'completato': [],  # Stato finale
        'annullato': [],   # Stato finale
    }
    
    return nuovo_stato in transizioni_consentite.get(stato_attuale, [])


@login_required
def cambia_stato_pagamento(request, pk):
    """Cambia lo stato di pagamento di un ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            nuovo_stato = data.get('stato_pagamento')
            
            # Valida che lo stato sia valido
            stati_validi = [choice[0] for choice in Ordine.STATO_PAGAMENTO_CHOICES]
            if nuovo_stato not in stati_validi:
                return JsonResponse({
                    'success': False,
                    'error': 'Stato pagamento non valido'
                })
            
            # Valida transizioni di stato pagamento
            if not _valida_transizione_stato_pagamento(ordine.stato_pagamento, nuovo_stato, ordine):
                return JsonResponse({
                    'success': False,
                    'error': 'Transizione di stato pagamento non consentita'
                })
            
            # Cambia lo stato
            vecchio_stato = ordine.stato_pagamento
            ordine.stato_pagamento = nuovo_stato
            ordine.save()
            
            # Log dell'operazione
            print(f"Ordine {ordine.numero_progressivo} stato pagamento cambiato da {vecchio_stato} a {nuovo_stato} da {request.user}")
            
            return JsonResponse({
                'success': True,
                'message': f'Stato pagamento cambiato a {ordine.get_stato_pagamento_display()}'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    })


def _valida_transizione_stato_pagamento(stato_attuale, nuovo_stato, ordine):
    """Valida se una transizione di stato pagamento è consentita"""
    # Controlli di base
    if stato_attuale == nuovo_stato:
        return False
    
    # Logica di transizione
    if nuovo_stato == 'pagato':
        # Può diventare pagato solo se l'importo pagato copre il totale
        return ordine.importo_pagato >= ordine.totale_finale
    elif nuovo_stato == 'parziale':
        # Può diventare parziale se c'è un pagamento parziale
        return ordine.importo_pagato > 0 and ordine.importo_pagato < ordine.totale_finale
    elif nuovo_stato == 'non_pagato':
        # Può tornare non pagato solo se non ci sono pagamenti
        return ordine.importo_pagato == 0
    elif nuovo_stato == 'differito':
        # Può diventare differito da qualsiasi stato
        return True
    
    return False