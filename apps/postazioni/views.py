from django.shortcuts import render, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView, TemplateView
)
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone
from .models import Postazione
from .forms import PostazioneForm
from apps.ordini.models import ItemOrdine, Ordine


class PostazioniListView(LoginRequiredMixin, ListView):
    model = Postazione
    template_name = 'postazioni/postazioni_list.html'
    context_object_name = 'postazioni'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Aggiungi statistiche per ogni postazione
        # Temporaneamente disabilitato - da riabilitare quando il modello ItemOrdine sarà completo
        for postazione in context['postazioni']:
            # postazione.ordini_in_coda_count = postazione.get_ordini_in_coda().count()
            # postazione.ordini_in_lavorazione = postazione.get_ordini_in_coda().filter(
            #     stato='in_lavorazione'
            # ).count()
            postazione.ordini_in_coda_count = 0  # Placeholder
            postazione.ordini_in_lavorazione = 0  # Placeholder
        
        return context


class PostazioneCreateView(LoginRequiredMixin, CreateView):
    model = Postazione
    form_class = PostazioneForm
    template_name = 'postazioni/postazione_form.html'
    success_url = reverse_lazy('postazioni:postazioni-list')
    
    def form_valid(self, form):
        messages.success(self.request, f'Postazione "{form.instance.nome}" creata con successo!')
        return super().form_valid(form)


class PostazioneUpdateView(LoginRequiredMixin, UpdateView):
    model = Postazione
    form_class = PostazioneForm
    template_name = 'postazioni/postazione_form.html'
    success_url = reverse_lazy('postazioni:postazioni-list')
    
    def form_valid(self, form):
        messages.success(self.request, f'Postazione "{form.instance.nome}" aggiornata con successo!')
        return super().form_valid(form)


class PostazioneDeleteView(LoginRequiredMixin, DeleteView):
    model = Postazione
    template_name = 'postazioni/postazione_confirm_delete.html'
    success_url = reverse_lazy('postazioni:postazioni-list')
    
    def delete(self, request, *args, **kwargs):
        postazione = self.get_object()
        messages.success(request, f'Postazione "{postazione.nome}" eliminata con successo!')
        return super().delete(request, *args, **kwargs)


class DashboardPostazione(LoginRequiredMixin, DetailView):
    model = Postazione
    template_name = 'postazioni/dashboard_postazione.html'
    context_object_name = 'postazione'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        postazione = self.object
        
        # Ordini in coda per questa postazione
        ordini_in_coda = postazione.get_ordini_in_coda()
        context['ordini_in_coda'] = ordini_in_coda
        
        # Statistiche giornaliere
        from django.utils import timezone
        from django.db import models
        from apps.ordini.models import ItemOrdine
        oggi = timezone.now().date()
        
        context['completati_oggi'] = ItemOrdine.objects.filter(
            postazione_assegnata=postazione,
            stato='completato',
            fine_lavorazione__date=oggi
        ).count()
        # Calcola tempo medio usando la proprietà durata_lavorazione del modello
        items_completati = ItemOrdine.objects.filter(
            postazione_assegnata=postazione,
            stato='completato',
            fine_lavorazione__date=oggi,
            inizio_lavorazione__isnull=False,
            fine_lavorazione__isnull=False
        )
        
        durate = []
        for item in items_completati:
            if item.durata_lavorazione:
                durate.append(item.durata_lavorazione)
        
        context['tempo_medio_oggi'] = sum(durate) / len(durate) if durate else 0
        
        # Prossimo ordine in coda
        context['prossimo_ordine'] = ordini_in_coda.filter(stato='in_attesa').first()
        
        # Ordine attualmente in lavorazione
        context['ordine_in_lavorazione'] = ordini_in_coda.filter(stato='in_lavorazione').first()
        
        return context


@login_required
def aggiorna_stato_item(request, postazione_id, item_id):
    """Aggiorna lo stato di un item ordine"""
    print(f'aggiorna_stato_item chiamato: postazione_id={postazione_id}, item_id={item_id}')
    print(f'Method: {request.method}')
    print(f'POST data: {request.POST}')
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    
    try:
        postazione = get_object_or_404(Postazione, id=postazione_id)
        print(f'Postazione trovata: {postazione}')
        
        item = get_object_or_404(ItemOrdine, id=item_id, postazione_assegnata=postazione)
        print(f'Item trovato: {item} - stato attuale: {item.stato}')
        
        nuovo_stato = request.POST.get('stato')
        print(f'Nuovo stato richiesto: {nuovo_stato}')
        
        if nuovo_stato not in ['in_attesa', 'in_lavorazione', 'completato']:
            return JsonResponse({'error': 'Stato non valido'}, status=400)
        
        # Aggiorna stato e timestamp
        from django.utils import timezone
        
        stato_precedente = item.stato
        print(f"Transizione: {stato_precedente} -> {nuovo_stato}")
        
        if nuovo_stato == 'in_lavorazione' and item.stato == 'in_attesa':
            item.inizio_lavorazione = timezone.now()
            print(f"Impostato inizio_lavorazione: {item.inizio_lavorazione}")
        elif nuovo_stato == 'completato' and item.stato == 'in_lavorazione':
            item.fine_lavorazione = timezone.now()
            print(f"Impostato fine_lavorazione: {item.fine_lavorazione}")
        elif nuovo_stato == 'in_attesa' and item.stato == 'in_lavorazione':
            # Quando si mette in pausa, mantieni i timestamp ma cambia lo stato
            # Non resettiamo inizio_lavorazione per non perdere il tempo già lavorato
            print(f"Messo in pausa, mantenendo inizio_lavorazione: {item.inizio_lavorazione}")
            pass
        
        item.stato = nuovo_stato
        item.save()
        print(f"Item salvato con stato: {item.stato}")
        
        # Verifica se tutto l'ordine è completato
        ordine = item.ordine
        vecchio_stato_ordine = ordine.stato
        ordine_cambiato = False
        
        if ordine.items.filter(stato__in=['in_attesa', 'in_lavorazione']).count() == 0:
            # Tutti gli item sono completati
            ordine.stato = 'completato'
            ordine.save()
            ordine_cambiato = True
        elif ordine.stato == 'in_attesa' and nuovo_stato == 'in_lavorazione':
            # Primo item che inizia lavorazione
            ordine.stato = 'in_lavorazione'
            ordine.save()
            ordine_cambiato = True
        elif ordine.stato == 'in_lavorazione' and nuovo_stato == 'in_attesa':
            # Verifica se ci sono ancora item in lavorazione
            items_in_lavorazione = ordine.items.filter(stato='in_lavorazione').count()
            if items_in_lavorazione == 0:
                # Nessun item in lavorazione, torna in attesa
                ordine.stato = 'in_attesa'
                ordine.save()
                ordine_cambiato = True
        
        # Invia aggiornamento WebSocket (opzionale)
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            if channel_layer:
                # Notifica la postazione specifica
                async_to_sync(channel_layer.group_send)(
                    f'postazione_{postazione_id}',
                    {
                        'type': 'item_status_update',
                        'item_id': item_id,
                        'stato': nuovo_stato,
                        'timestamp': timezone.now().isoformat()
                    }
                )
                
                # Se lo stato dell'ordine è cambiato, notifica anche le altre interfacce
                if ordine_cambiato:
                    # Notifica tutte le postazioni che hanno items di questo ordine
                    postazioni_coinvolte = set()
                    for ord_item in ordine.items.all():
                        if ord_item.postazione_assegnata:
                            postazioni_coinvolte.add(ord_item.postazione_assegnata.id)
                    
                    # Invia notifica a ogni postazione coinvolta
                    for post_id in postazioni_coinvolte:
                        async_to_sync(channel_layer.group_send)(
                            f'postazione_{post_id}',
                            {
                                'type': 'order_status_update',
                                'ordine_id': ordine.id,
                                'numero_progressivo': ordine.numero_progressivo,
                                'vecchio_stato': vecchio_stato_ordine,
                                'nuovo_stato': ordine.stato,
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
                            'vecchio_stato': vecchio_stato_ordine,
                            'nuovo_stato': ordine.stato,
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
        
        # Log dettagliato per debug
        print(f"Item {item.id} cambiato da stato precedente a {nuovo_stato}")
        print(f"Stato salvato nel database: {item.stato}")
        if ordine_cambiato:
            print(f"Ordine {ordine.numero_progressivo} cambiato da {vecchio_stato_ordine} a {ordine.stato}")
            print(f"Items in lavorazione dopo il cambio: {ordine.items.filter(stato='in_lavorazione').count()}")
        else:
            print(f"Stato ordine non cambiato, rimane: {ordine.stato}")
        
        return JsonResponse({
            'success': True,
            'nuovo_stato': nuovo_stato,
            'stato_display': item.get_stato_display(),
            'ordine_stato': ordine.stato,
            'ordine_cambiato': ordine_cambiato
        })
        
    except Exception as e:
        import traceback
        print(f'Errore in aggiorna_stato_item: {e}')
        print(f'Traceback: {traceback.format_exc()}')
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def ora_server(request):
    """Restituisce l'ora del server in fuso orario italiano"""
    ora_corrente = timezone.now()
    return JsonResponse({
        'timestamp': ora_corrente.isoformat(),
        'ora_locale': ora_corrente.strftime('%H:%M'),
        'data_ora_locale': ora_corrente.strftime('%d/%m/%Y %H:%M'),
        'timezone': str(ora_corrente.tzinfo)
    })


@login_required
def stampa_comanda_postazione(request, postazione_id, ordine_numero):
    """Stampa comanda per una specifica postazione"""
    postazione = get_object_or_404(Postazione, id=postazione_id)
    ordine = get_object_or_404(Ordine, numero_progressivo=ordine_numero)
    
    # Filtra solo gli item per questa postazione
    items_postazione = ordine.items.filter(postazione_assegnata=postazione)
    
    if not items_postazione.exists():
        messages.error(request, 'Nessun servizio per questa postazione in questo ordine.')
        return redirect('postazioni:dashboard-postazione', pk=postazione_id)
    
    # Logica di stampa (da implementare con libreria escpos)
    try:
        if postazione.stampante_comande:
            # Implementare stampa reale
            context = {
                'ordine': ordine,
                'postazione': postazione,
                'items': items_postazione,
                'timestamp': timezone.now()
            }
            # print_comanda(postazione.stampante_comande, context)
            messages.success(request, f'Comanda stampata su {postazione.stampante_comande.nome}')
        else:
            messages.warning(request, 'Nessuna stampante configurata per questa postazione')
    except Exception as e:
        messages.error(request, f'Errore durante la stampa: {str(e)}')
    
    return redirect('postazioni:dashboard-postazione', pk=postazione_id)


class DashboardTVView(LoginRequiredMixin, TemplateView):
    template_name = 'postazioni/dashboard_tv.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Recupera tutti gli ordini con i loro stati che hanno items assegnati a postazioni
        ordini = Ordine.objects.select_related('cliente').prefetch_related(
            'items__servizio_prodotto',
            'items__postazione_assegnata'
        ).filter(
            stato__in=['in_attesa', 'in_lavorazione'],
            items__postazione_assegnata__isnull=False
        ).distinct().order_by('numero_progressivo')
        
        # Organizza gli ordini per stato
        ordini_per_stato = {
            'in_attesa': [],
            'in_lavorazione': [],
        }
        
        for ordine in ordini:
            ordini_per_stato[ordine.stato].append(ordine)
        
        context['ordini_per_stato'] = ordini_per_stato
        context['postazioni'] = Postazione.objects.filter(attiva=True)
        
        return context


@login_required
def dashboard_tv_data(request):
    """API endpoint per aggiornare i dati del dashboard TV in tempo reale"""
    
    # Filtra solo gli ordini che hanno almeno un item assegnato a una postazione
    ordini = Ordine.objects.select_related('cliente').prefetch_related(
        'items__servizio_prodotto',
        'items__postazione_assegnata'
    ).filter(
        stato__in=['in_attesa', 'in_lavorazione'],
        items__postazione_assegnata__isnull=False
    ).distinct().order_by('numero_progressivo')
    
    ordini_data = []
    for ordine in ordini:
        # Calcola ora consegna prevista o usa data_ora
        ora_consegna = ordine.ora_consegna_prevista
        if ora_consegna:
            ora_consegna_str = ora_consegna.strftime('%H:%M')
            # Se è DateTime, usa direttamente, altrimenti combina con data_ora
            if hasattr(ora_consegna, 'date'):
                ora_consegna_completa = ora_consegna.isoformat()
            else:
                # Se è solo Time, combina con la data dell'ordine
                from datetime import datetime, time
                if isinstance(ora_consegna, time):
                    dt_consegna = datetime.combine(ordine.data_ora.date(), ora_consegna)
                    ora_consegna_completa = dt_consegna.isoformat()
                else:
                    ora_consegna_completa = ordine.data_ora.isoformat()
        else:
            ora_consegna_str = ordine.data_ora.strftime('%H:%M')
            ora_consegna_completa = ordine.data_ora.isoformat()
        
        # Ora consegna richiesta
        ora_consegna_richiesta_str = ''
        if ordine.ora_consegna_richiesta:
            ora_consegna_richiesta_str = ordine.ora_consegna_richiesta.strftime('%H:%M')
        
        # Converti tutti i timestamp in fuso orario locale per consistenza
        from django.utils import timezone as tz
        import pytz
        
        rome_tz = pytz.timezone('Europe/Rome')
        data_ora_locale = ordine.data_ora.astimezone(rome_tz)
        
        ordine_data = {
            'id': ordine.id,
            'numero_progressivo': ordine.numero_progressivo,
            'cliente': ordine.cliente.nome if ordine.cliente else 'Cliente Anonimo',
            'tipo_auto': ordine.tipo_auto if ordine.tipo_auto else '',
            'stato': ordine.stato,
            'stato_display': ordine.get_stato_display(),
            'stato_pagamento': ordine.stato_pagamento,
            'data_ora': data_ora_locale.strftime('%H:%M'),
            'data_ora_completa': ordine.data_ora.isoformat(),
            'data_ora_server_formatted': data_ora_locale.isoformat(),
            'ora_consegna_prevista': ora_consegna_str,
            'ora_consegna_prevista_completa': ora_consegna_completa,
            'ora_consegna_richiesta': ora_consegna_richiesta_str,
            'tipo_consegna': ordine.tipo_consegna,
            'totale_finale': str(ordine.totale_finale),
            'nota': ordine.nota,
            'items': []
        }
        
        for item in ordine.items.all():
            item_data = {
                'id': item.id,
                'servizio': item.servizio_prodotto.titolo,
                'quantita': item.quantita,
                'stato': item.stato,
                'stato_display': item.get_stato_display(),
                'postazione': item.postazione_assegnata.nome if item.postazione_assegnata else 'Non assegnata',
                'postazione_id': item.postazione_assegnata.id if item.postazione_assegnata else None,
            }
            ordine_data['items'].append(item_data)
        
        ordini_data.append(ordine_data)
    
    # Timestamp del server in fuso orario locale per sincronizzare tutti i dispositivi
    from django.utils import timezone as tz
    import pytz
    
    rome_tz = pytz.timezone('Europe/Rome')
    server_time_local = tz.now().astimezone(rome_tz)
    
    return JsonResponse({
        'ordini': ordini_data,
        'timestamp': timezone.now().isoformat(),
        'server_time_local': server_time_local.isoformat(),
        'server_time_formatted': server_time_local.strftime('%H:%M:%S')
    })