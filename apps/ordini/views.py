from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, DetailView, TemplateView
)
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db.models import Q, Sum, Count, F, Case, When, Value, CharField
from django.utils import timezone
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
import json
from datetime import timedelta, datetime
from .models import Ordine, ItemOrdine, Pagamento, ConfigurazionePianificazione
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
            nuovo_cliente_data = data.get('nuovo_cliente')
            tipo_cliente = nuovo_cliente_data.get('tipo')
            
            if tipo_cliente == 'privato':
                # Usa ClienteQuickForm per clienti privati
                cliente_form = ClienteQuickForm(nuovo_cliente_data)
                if cliente_form.is_valid():
                    cliente = cliente_form.save()
                else:
                    return JsonResponse({
                        'error': 'Dati cliente non validi',
                        'form_errors': cliente_form.errors
                    }, status=400)
            elif tipo_cliente == 'azienda':
                # Crea cliente azienda manualmente
                try:
                    email = nuovo_cliente_data.get('email')
                    if not email or email.strip() == '':
                        email = None
                    
                    cliente = Cliente.objects.create(
                        tipo='azienda',
                        ragione_sociale=nuovo_cliente_data.get('ragione_sociale', ''),
                        partita_iva=nuovo_cliente_data.get('partita_iva', ''),
                        codice_sdi=nuovo_cliente_data.get('codice_sdi', ''),
                        indirizzo=nuovo_cliente_data.get('indirizzo', ''),
                        telefono=nuovo_cliente_data.get('telefono', ''),
                        email=email,
                    )
                except Exception as e:
                    return JsonResponse({
                        'error': f'Errore nella creazione del cliente azienda: {str(e)}'
                    }, status=400)
            else:
                return JsonResponse({
                    'error': 'Tipo cliente non valido'
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
        
        # Invia notifica WebSocket alle postazioni e alla lista ordini
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            channel_layer = get_channel_layer()
            if channel_layer:
                # Notifica alle postazioni
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

                # Notifica alla lista ordini per aggiornamento automatico
                async_to_sync(channel_layer.group_send)(
                    'ordini_list',
                    {
                        'type': 'nuovo_ordine',
                        'ordine_id': ordine.id,
                        'numero_progressivo': ordine.numero_progressivo,
                        'timestamp': timezone.now().isoformat()
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

    def get_queryset(self):
        # Queryset base non utilizzato in questo caso, ma richiesto da ListView
        return Ordine.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Ottieni i filtri
        stato = self.request.GET.get('stato')
        data_da = self.request.GET.get('data_da')
        data_a = self.request.GET.get('data_a')
        cliente = self.request.GET.get('cliente')

        # Query base per tutti gli ordini
        queryset = Ordine.objects.select_related('cliente', 'operatore').prefetch_related(
            'items__servizio_prodotto', 'items__postazione_cq', 'items__aggiunto_da', 'pagamenti'
        )

        # Applica filtri temporali
        if not any([stato, data_da, data_a, cliente]):
            oggi = timezone.now().date()
            queryset = queryset.filter(data_ora__date=oggi)
        else:
            if data_da:
                queryset = queryset.filter(data_ora__date__gte=data_da)
            if data_a:
                queryset = queryset.filter(data_ora__date__lte=data_a)
            if cliente:
                queryset = queryset.filter(cliente_id=cliente)

        # Separa ordini attivi da completati
        if stato:
            # Se c'è un filtro stato specifico, usalo
            if stato == 'completato':
                context['ordini_attivi'] = Ordine.objects.none()
                context['ordini_da_ritirare'] = queryset.filter(stato='completato', auto_ritirata=False).order_by('-data_ora')
                context['ordini_completati'] = queryset.filter(stato='completato', auto_ritirata=True).order_by('-data_ora')
            else:
                # Ordini immediati e programmati separati, poi concatenati
                ordini_immediati = queryset.filter(
                    stato=stato,
                    tipo_consegna='immediata'
                ).order_by('data_ora')

                ordini_programmati = queryset.filter(
                    stato=stato,
                    tipo_consegna='programmata'
                ).order_by('ora_consegna_richiesta')

                # Concatena: prima immediati, poi programmati
                from itertools import chain
                context['ordini_attivi'] = list(chain(ordini_immediati, ordini_programmati))
                context['ordini_da_ritirare'] = Ordine.objects.none()
                context['ordini_completati'] = Ordine.objects.none()
        else:
            # Senza filtro stato, mostra ordini attivi e completati separatamente
            # Ordini con priorita manuale vengono prima, poi il resto nell'ordine originale
            ordini_con_priorita = queryset.filter(
                stato__in=['in_attesa', 'in_lavorazione'],
                priorita__gt=0,
            ).order_by('priorita')

            ordini_senza_priorita_imm = queryset.filter(
                stato__in=['in_attesa', 'in_lavorazione'],
                priorita=0,
                tipo_consegna='immediata',
            ).order_by('data_ora')

            ordini_senza_priorita_prog = queryset.filter(
                stato__in=['in_attesa', 'in_lavorazione'],
                priorita=0,
                tipo_consegna='programmata',
            ).order_by('ora_consegna_richiesta')

            from itertools import chain
            context['ordini_attivi'] = list(chain(
                ordini_con_priorita, ordini_senza_priorita_imm, ordini_senza_priorita_prog
            ))

            # Ordini da ritirare: completati ma NON ancora ritirati
            context['ordini_da_ritirare'] = queryset.filter(
                stato='completato',
                auto_ritirata=False
            ).order_by('-data_ora')

            # Ordini completati: completati E già ritirati
            context['ordini_completati'] = queryset.filter(
                stato='completato',
                auto_ritirata=True
            ).order_by('-data_ora')

        # Calcola statistiche finanziarie
        # Usa lo stesso queryset filtrato per le statistiche
        from decimal import Decimal

        # Totale ordini (somma dei totale_finale)
        totale_ordini = queryset.aggregate(
            totale=Sum('totale_finale')
        )['totale'] or Decimal('0.00')

        # Totale incassato in contanti
        totale_contanti = Pagamento.objects.filter(
            ordine__in=queryset,
            metodo='contanti'
        ).aggregate(totale=Sum('importo'))['totale'] or Decimal('0.00')

        # Totale incassato con carte (carta + bancomat)
        totale_carte = Pagamento.objects.filter(
            ordine__in=queryset,
            metodo__in=['carta', 'bancomat']
        ).aggregate(totale=Sum('importo'))['totale'] or Decimal('0.00')

        # Totale non ancora incassato (saldo dovuto)
        # Calcola manualmente perché saldo_dovuto è una property
        totale_non_incassato = Decimal('0.00')
        for ordine in queryset:
            if ordine.saldo_dovuto > 0:
                totale_non_incassato += ordine.saldo_dovuto

        context['statistiche'] = {
            'totale_ordini': totale_ordini,
            'totale_contanti': totale_contanti,
            'totale_carte': totale_carte,
            'totale_non_incassato': totale_non_incassato,
            'totale_incassato': totale_contanti + totale_carte,
        }

        # Prenotazioni del giorno (ancora da fare checkin)
        from apps.prenotazioni.models import Prenotazione
        oggi = timezone.now().date()
        context['prenotazioni_oggi'] = (
            Prenotazione.objects
            .filter(
                slot__data=oggi,
                stato__in=['confermata', 'in_attesa'],
                ordine__isnull=True,
            )
            .select_related('cliente', 'slot')
            .prefetch_related('servizi')
            .order_by('slot__ora_inizio')
        )

        return context


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
        
        # Lista clienti per il modal di modifica
        context['clienti'] = Cliente.objects.all().order_by('cognome', 'nome', 'ragione_sociale')
        
        # Categorie e servizi per la modifica dei servizi
        context['categorie'] = Categoria.objects.filter(attiva=True).prefetch_related('servizioprodotto_set')
        
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

                # Notifica WebSocket alla lista ordini
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync

                    channel_layer = get_channel_layer()
                    if channel_layer:
                        async_to_sync(channel_layer.group_send)(
                            'ordini_list',
                            {
                                'type': 'pagamento_aggiunto',
                                'ordine_id': ordine.id,
                                'numero_progressivo': ordine.numero_progressivo,
                                'timestamp': timezone.now().isoformat()
                            }
                        )
                except Exception as e:
                    print(f'Errore WebSocket: {e}')

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

    # Includi anche gli items
    items = []
    for item in ordine.items.all():
        items.append({
            'id': item.id,
            'servizio_id': item.servizio_prodotto.id,
            'servizio_nome': item.servizio_prodotto.titolo,
            'quantita': item.quantita,
            'prezzo_unitario': float(item.prezzo_unitario),
            'subtotale': float(item.subtotale),
            'stato': item.stato
        })

    return JsonResponse({
        'id': ordine.id,
        'numero_progressivo': ordine.numero_progressivo,
        'totale_finale': float(ordine.totale_finale),
        'importo_pagato': float(ordine.importo_pagato),
        'saldo_dovuto': float(ordine.saldo_dovuto),
        'stato_pagamento': ordine.stato_pagamento,
        'stato_pagamento_display': ordine.get_stato_pagamento_display(),
        'cliente': ordine.cliente.nome_completo if ordine.cliente else 'Cliente anonimo',
        'cliente_id': ordine.cliente.id if ordine.cliente else None,
        'tipo_consegna': ordine.tipo_consegna,
        'ora_consegna_richiesta': ordine.ora_consegna_richiesta.strftime('%H:%M') if ordine.ora_consegna_richiesta else None,
        'tipo_auto': ordine.tipo_auto or '',
        'data_ora': ordine.data_ora.strftime('%d/%m/%Y %H:%M'),
        'items': items
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
            
            # Aggiorna automaticamente lo stato degli item quando necessario
            if nuovo_stato == 'completato':
                # Se l'ordine è completato, completa tutti gli item
                for item in ordine.items.all():
                    if item.stato != 'completato':
                        item.stato = 'completato'
                        if not item.fine_lavorazione:
                            item.fine_lavorazione = timezone.now()
                        item.save()
                        print(f"Item {item.id} automaticamente completato per ordine completato")
            elif nuovo_stato == 'annullato':
                # Se l'ordine è annullato, potremmo voler gestire gli item diversamente
                # Per ora li lasciamo come sono, ma potremmo aggiungere logica specifica
                pass
            
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


@login_required
@transaction.atomic
def modifica_ordine(request, pk):
    """Modifica i dati di un ordine (cliente, tipo consegna, tipo auto)"""
    ordine = get_object_or_404(Ordine, pk=pk)

    # Verifica che l'ordine sia modificabile (solo gli annullati sono bloccati)
    if ordine.stato == 'annullato':
        return JsonResponse({
            'success': False,
            'error': 'Non è possibile modificare un ordine annullato'
        }, status=400)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Traccia i cambiamenti
            cambiamenti = []

            # Modifica cliente (solo se presente nel JSON)
            if 'cliente_id' in data or 'nuovo_cliente' in data:
                # Verifica se si sta creando un nuovo cliente
                if data.get('nuovo_cliente'):
                    try:
                        tipo = data.get('tipo')
                        if tipo == 'privato':
                            nuovo_cliente = Cliente.objects.create(
                                tipo='privato',
                                nome=data.get('nome'),
                                cognome=data.get('cognome'),
                                telefono=data.get('telefono'),
                                email=data.get('email', '')
                            )
                        elif tipo == 'azienda':
                            nuovo_cliente = Cliente.objects.create(
                                tipo='azienda',
                                ragione_sociale=data.get('ragione_sociale'),
                                partita_iva=data.get('partita_iva'),
                                codice_sdi=data.get('codice_sdi', ''),
                                indirizzo=data.get('indirizzo', ''),
                                telefono=data.get('telefono'),
                                email=data.get('email', '')
                            )
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': 'Tipo cliente non valido'
                            }, status=400)

                        vecchio_cliente = ordine.cliente.nome_completo if ordine.cliente else "Anonimo"
                        ordine.cliente = nuovo_cliente
                        cambiamenti.append(f"Cliente: {vecchio_cliente} → {nuovo_cliente.nome_completo} (nuovo)")
                    except Exception as e:
                        return JsonResponse({
                            'success': False,
                            'error': f'Errore nella creazione del cliente: {str(e)}'
                        }, status=400)
                else:
                    # Cliente esistente
                    cliente_id = data.get('cliente_id')
                    if cliente_id:
                        try:
                            nuovo_cliente = Cliente.objects.get(id=cliente_id)
                            if ordine.cliente != nuovo_cliente:
                                vecchio_cliente = ordine.cliente.nome_completo if ordine.cliente else "Anonimo"
                                ordine.cliente = nuovo_cliente
                                cambiamenti.append(f"Cliente: {vecchio_cliente} → {nuovo_cliente.nome_completo}")
                        except Cliente.DoesNotExist:
                            return JsonResponse({
                                'success': False,
                                'error': 'Cliente non trovato'
                            }, status=400)
                    else:
                        # Cliente vuoto = ordine anonimo
                        if ordine.cliente:
                            vecchio_cliente = ordine.cliente.nome_completo
                            ordine.cliente = None
                            cambiamenti.append(f"Cliente: {vecchio_cliente} → Anonimo")
            
            # Modifica tipo consegna
            tipo_consegna = data.get('tipo_consegna')
            if tipo_consegna and tipo_consegna in dict(Ordine.TIPO_CONSEGNA_CHOICES):
                # Gestisci ora consegna in base al tipo (prima del cambio tipo)
                ora_consegna = data.get('ora_consegna_richiesta')
                
                if tipo_consegna == 'programmata' and not ora_consegna:
                    # Se cambia a programmata ma non ha fornito ora, errore
                    return JsonResponse({
                        'success': False,
                        'error': 'Ora consegna richiesta è obbligatoria per consegna programmata'
                    }, status=400)
                
                # Ora gestisci il cambio di tipo consegna
                if ordine.tipo_consegna != tipo_consegna:
                    vecchio_tipo = ordine.get_tipo_consegna_display()
                    ordine.tipo_consegna = tipo_consegna
                    cambiamenti.append(f"Tipo consegna: {vecchio_tipo} → {ordine.get_tipo_consegna_display()}")
                
                # Gestisci l'ora consegna
                if tipo_consegna == 'immediata':
                    if ordine.ora_consegna_richiesta:
                        ordine.ora_consegna_richiesta = None
                        cambiamenti.append("Ora consegna rimossa (consegna immediata)")
                elif tipo_consegna == 'programmata' and ora_consegna:
                    from datetime import datetime
                    try:
                        # Converte la stringa ora in oggetto time
                        ora_obj = datetime.strptime(ora_consegna, '%H:%M').time()
                        if ordine.ora_consegna_richiesta != ora_obj:
                            vecchia_ora = ordine.ora_consegna_richiesta.strftime('%H:%M') if ordine.ora_consegna_richiesta else "Non specificata"
                            ordine.ora_consegna_richiesta = ora_obj
                            cambiamenti.append(f"Ora consegna: {vecchia_ora} → {ora_consegna}")
                    except ValueError:
                        return JsonResponse({
                            'success': False,
                            'error': 'Formato ora non valido'
                        }, status=400)
            
            # Modifica tipo auto (solo se presente nel JSON)
            if 'tipo_auto' in data:
                tipo_auto = data.get('tipo_auto', '').strip()
                if ordine.tipo_auto != tipo_auto:
                    vecchio_tipo_auto = ordine.tipo_auto or "Non specificato"
                    ordine.tipo_auto = tipo_auto
                    nuovo_tipo_auto = tipo_auto or "Non specificato"
                    cambiamenti.append(f"Tipo auto: {vecchio_tipo_auto} → {nuovo_tipo_auto}")

            # Modifica nota (solo se presente nel JSON)
            if 'nota' in data:
                nota = data.get('nota', '').strip()
                if ordine.nota != nota:
                    vecchia_nota = ordine.nota or "Nessuna nota"
                    ordine.nota = nota
                    nuova_nota = nota or "Nessuna nota"
                    cambiamenti.append(f"Note: {vecchia_nota[:30]}... → {nuova_nota[:30]}...")

            # Salva solo se ci sono stati cambiamenti
            if cambiamenti:
                ordine.save()

                # Log dell'operazione
                print(f"Ordine {ordine.numero_progressivo} modificato da {request.user}: {', '.join(cambiamenti)}")

                # Notifica WebSocket alla lista ordini per qualsiasi modifica
                if cambiamenti:
                    try:
                        from channels.layers import get_channel_layer
                        from asgiref.sync import async_to_sync

                        channel_layer = get_channel_layer()
                        if channel_layer:
                            async_to_sync(channel_layer.group_send)(
                                'ordini_list',
                                {
                                    'type': 'ordine_modificato',
                                    'ordine_id': ordine.id,
                                    'numero_progressivo': ordine.numero_progressivo,
                                    'timestamp': timezone.now().isoformat()
                                }
                            )
                    except Exception as e:
                        print(f'Errore WebSocket: {e}')

                return JsonResponse({
                    'success': True,
                    'message': f'Ordine modificato con successo. Cambiamenti: {", ".join(cambiamenti)}'
                })
            else:
                return JsonResponse({
                    'success': True,
                    'message': 'Nessuna modifica apportata'
                })
                
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Errore durante la modifica: {str(e)}'
            }, status=500)
    
    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    }, status=405)


@login_required
def aggiungi_item_ordine(request, pk):
    """Aggiunge un nuovo servizio/prodotto a un ordine esistente"""
    print(f"DEBUG: aggiungi_item_ordine chiamato - ordine_id={pk}, metodo={request.method}, user={request.user}")

    ordine = get_object_or_404(Ordine, pk=pk)
    print(f"DEBUG: Ordine trovato - numero={ordine.numero_progressivo}, stato={ordine.stato}")

    # Verifica che l'ordine sia modificabile (solo gli annullati sono bloccati)
    if ordine.stato == 'annullato':
        return JsonResponse({
            'success': False,
            'error': 'Non è possibile modificare un ordine annullato'
        }, status=400)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            # Ottieni il servizio/prodotto da aggiungere
            servizio_id = data.get('servizio_id')
            quantita = data.get('quantita', 1)
            prezzo = data.get('prezzo')

            if not servizio_id:
                return JsonResponse({
                    'success': False,
                    'error': 'Servizio non specificato'
                }, status=400)

            try:
                servizio = ServizioProdotto.objects.get(id=servizio_id, attivo=True)
            except ServizioProdotto.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Servizio non trovato'
                }, status=400)

            # Verifica disponibilità
            if not servizio.disponibile:
                return JsonResponse({
                    'success': False,
                    'error': 'Il servizio/prodotto selezionato non è disponibile'
                }, status=400)

            # Valida quantità
            try:
                quantita = int(quantita)
                if quantita <= 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'La quantità deve essere maggiore di 0'
                    }, status=400)
            except (ValueError, TypeError):
                return JsonResponse({
                    'success': False,
                    'error': 'Quantità non valida'
                }, status=400)

            # Verifica disponibilità per prodotti
            if servizio.tipo == 'prodotto' and servizio.quantita_disponibile > 0:
                if servizio.quantita_disponibile < quantita:
                    return JsonResponse({
                        'success': False,
                        'error': f'Disponibili solo {servizio.quantita_disponibile} unità'
                    }, status=400)

            # Valida prezzo
            if prezzo:
                try:
                    from decimal import Decimal
                    prezzo = Decimal(str(prezzo))
                    if prezzo < 0:
                        return JsonResponse({
                            'success': False,
                            'error': 'Il prezzo non può essere negativo'
                        }, status=400)
                except (ValueError, TypeError):
                    return JsonResponse({
                        'success': False,
                        'error': 'Prezzo non valido'
                    }, status=400)
            else:
                prezzo = servizio.prezzo

            # Assegna postazione se è un servizio
            postazione_assegnata = None
            if servizio.tipo == 'servizio':
                postazioni_disponibili = servizio.postazioni.filter(attiva=True)
                if postazioni_disponibili.exists():
                    # Assegna alla postazione con meno carico
                    postazione_assegnata = min(
                        postazioni_disponibili,
                        key=lambda p: p.get_ordini_in_coda().count()
                    )

            # Crea il nuovo item
            print(f"DEBUG: Creazione item - servizio_id={servizio.id}, quantita={quantita}, prezzo={prezzo}")

            nuovo_item = ItemOrdine.objects.create(
                ordine=ordine,
                servizio_prodotto=servizio,
                quantita=quantita,
                prezzo_unitario=prezzo,
                postazione_assegnata=postazione_assegnata
            )

            print(f"DEBUG: Item creato con ID={nuovo_item.id}")

            # Ricalcola i totali dell'ordine
            items_count = ordine.items.count()
            print(f"DEBUG: Numero items nell'ordine: {items_count}")

            ordine.totale = sum(item.subtotale for item in ordine.items.all())
            print(f"DEBUG: Nuovo totale ordine: {ordine.totale}")

            # Ricalcola sconto se applicato
            if ordine.sconto_applicato:
                ordine.importo_sconto = ordine.sconto_applicato.calcola_sconto(ordine.totale)
            else:
                ordine.importo_sconto = 0

            ordine.totale_finale = ordine.totale - ordine.importo_sconto
            print(f"DEBUG: Totale finale: {ordine.totale_finale}")

            # Salva l'ordine con i nuovi totali
            ordine.save()
            print(f"DEBUG: Ordine salvato con nuovi totali")

            # Aggiorna stato pagamento (che salva di nuovo l'ordine)
            ordine.aggiorna_stato_pagamento()
            print(f"DEBUG: Stato pagamento aggiornato")

            # Log dell'operazione
            print(f"SUCCESS: Aggiunto servizio {servizio.titolo} (Qtà: {quantita}) all'ordine {ordine.numero_progressivo} da {request.user}")

            return JsonResponse({
                'success': True,
                'message': f'Servizio "{servizio.titolo}" aggiunto con successo',
                'item_id': nuovo_item.id,
                'nuovo_totale': float(ordine.totale_finale)
            })

        except json.JSONDecodeError as e:
            print(f"ERROR: JSONDecodeError - {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            }, status=400)
        except Exception as e:
            import traceback
            print(f"ERROR: Exception durante aggiunta item - {str(e)}")
            print(f"ERROR: Traceback - {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'Errore durante l\'aggiunta: {str(e)}'
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    }, status=405)


@login_required
@transaction.atomic
def modifica_item_ordine(request, pk):
    """Modifica un item specifico dell'ordine (servizio e prezzo)"""
    ordine = get_object_or_404(Ordine, pk=pk)

    # Verifica che l'ordine sia modificabile (solo gli annullati sono bloccati)
    if ordine.stato == 'annullato':
        return JsonResponse({
            'success': False,
            'error': 'Non è possibile modificare un ordine annullato'
        }, status=400)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Ottieni l'item da modificare
            item_id = data.get('item_id')
            if not item_id:
                return JsonResponse({
                    'success': False,
                    'error': 'ID item non fornito'
                }, status=400)
            
            try:
                item = ItemOrdine.objects.get(id=item_id, ordine=ordine)
            except ItemOrdine.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Item non trovato'
                }, status=400)
            
            # Verifica che l'item non sia già completato
            if item.stato == 'completato':
                return JsonResponse({
                    'success': False,
                    'error': 'Non è possibile modificare un servizio già completato'
                }, status=400)
            
            # Traccia i cambiamenti
            cambiamenti = []
            
            # Nuovo servizio/prodotto
            nuovo_servizio_id = data.get('nuovo_servizio_id')
            if nuovo_servizio_id:
                try:
                    nuovo_servizio = ServizioProdotto.objects.get(id=nuovo_servizio_id, attivo=True)
                    
                    # Verifica disponibilità
                    if not nuovo_servizio.disponibile:
                        return JsonResponse({
                            'success': False,
                            'error': 'Il servizio/prodotto selezionato non è disponibile'
                        }, status=400)
                    
                    if item.servizio_prodotto != nuovo_servizio:
                        vecchio_servizio = item.servizio_prodotto.titolo
                        item.servizio_prodotto = nuovo_servizio
                        cambiamenti.append(f"Servizio: {vecchio_servizio} → {nuovo_servizio.titolo}")
                        
                        # Riassegna postazione se necessario per i servizi
                        if nuovo_servizio.tipo == 'servizio':
                            postazioni_disponibili = nuovo_servizio.postazioni.filter(attiva=True)
                            if postazioni_disponibili.exists():
                                # Assegna alla postazione con meno carico
                                nuova_postazione = min(
                                    postazioni_disponibili,
                                    key=lambda p: p.get_ordini_in_coda().count()
                                )
                                if item.postazione_assegnata != nuova_postazione:
                                    vecchia_postazione = item.postazione_assegnata.nome if item.postazione_assegnata else "Nessuna"
                                    item.postazione_assegnata = nuova_postazione
                                    cambiamenti.append(f"Postazione: {vecchia_postazione} → {nuova_postazione.nome}")
                        else:
                            # Per i prodotti, rimuovi la postazione
                            if item.postazione_assegnata:
                                cambiamenti.append(f"Postazione rimossa (prodotto)")
                                item.postazione_assegnata = None
                                
                except ServizioProdotto.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'error': 'Servizio/prodotto non trovato'
                    }, status=400)
            
            # Nuova quantità
            nuova_quantita = data.get('nuova_quantita')
            if nuova_quantita:
                try:
                    nuova_quantita = int(nuova_quantita)
                    if nuova_quantita <= 0:
                        return JsonResponse({
                            'success': False,
                            'error': 'La quantità deve essere maggiore di 0'
                        }, status=400)
                    
                    # Verifica disponibilità per prodotti
                    if item.servizio_prodotto.tipo == 'prodotto' and item.servizio_prodotto.quantita_disponibile > 0:
                        if item.servizio_prodotto.quantita_disponibile < nuova_quantita:
                            return JsonResponse({
                                'success': False,
                                'error': f'Disponibili solo {item.servizio_prodotto.quantita_disponibile} unità'
                            }, status=400)
                    
                    if item.quantita != nuova_quantita:
                        vecchia_quantita = item.quantita
                        item.quantita = nuova_quantita
                        cambiamenti.append(f"Quantità: {vecchia_quantita} → {nuova_quantita}")
                        
                except (ValueError, TypeError):
                    return JsonResponse({
                        'success': False,
                        'error': 'Quantità non valida'
                    }, status=400)
            
            # Nuovo prezzo
            nuovo_prezzo = data.get('nuovo_prezzo')
            if nuovo_prezzo:
                try:
                    from decimal import Decimal
                    nuovo_prezzo = Decimal(str(nuovo_prezzo))
                    if nuovo_prezzo < 0:
                        return JsonResponse({
                            'success': False,
                            'error': 'Il prezzo non può essere negativo'
                        }, status=400)
                    
                    if item.prezzo_unitario != nuovo_prezzo:
                        vecchio_prezzo = item.prezzo_unitario
                        item.prezzo_unitario = nuovo_prezzo
                        cambiamenti.append(f"Prezzo: €{vecchio_prezzo} → €{nuovo_prezzo}")
                        
                except (ValueError, TypeError):
                    return JsonResponse({
                        'success': False,
                        'error': 'Prezzo non valido'
                    }, status=400)
            
            # Salva solo se ci sono stati cambiamenti
            if cambiamenti:
                item.save()
                
                # Ricalcola i totali dell'ordine
                ordine.totale = sum(item.subtotale for item in ordine.items.all())
                
                # Ricalcola sconto se applicato
                if ordine.sconto_applicato:
                    ordine.importo_sconto = ordine.sconto_applicato.calcola_sconto(ordine.totale)
                else:
                    ordine.importo_sconto = 0
                
                ordine.totale_finale = ordine.totale - ordine.importo_sconto
                
                # Aggiorna stato pagamento
                ordine.aggiorna_stato_pagamento()
                ordine.save()
                
                # Log dell'operazione
                print(f"Item {item.id} dell'ordine {ordine.numero_progressivo} modificato da {request.user}: {', '.join(cambiamenti)}")

                return JsonResponse({
                    'success': True,
                    'message': f'Servizio modificato con successo. Cambiamenti: {", ".join(cambiamenti)}'
                })
            else:
                return JsonResponse({
                    'success': True,
                    'message': 'Nessuna modifica apportata'
                })

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Errore durante la modifica: {str(e)}'
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    }, status=405)


@login_required
def elimina_item_ordine(request, pk):
    """Elimina un item specifico dall'ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)

    # Verifica che l'ordine sia modificabile (solo gli annullati sono bloccati)
    if ordine.stato == 'annullato':
        return JsonResponse({
            'success': False,
            'error': 'Non è possibile modificare un ordine annullato'
        }, status=400)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            # Ottieni l'item da eliminare
            item_id = data.get('item_id')
            if not item_id:
                return JsonResponse({
                    'success': False,
                    'error': 'ID item non fornito'
                }, status=400)

            try:
                item = ItemOrdine.objects.get(id=item_id, ordine=ordine)
            except ItemOrdine.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Item non trovato'
                }, status=400)

            # Verifica che l'item non sia già completato
            if item.stato == 'completato':
                return JsonResponse({
                    'success': False,
                    'error': 'Non è possibile eliminare un servizio già completato'
                }, status=400)

            # Verifica che ci sia almeno un altro item nell'ordine
            if ordine.items.count() <= 1:
                return JsonResponse({
                    'success': False,
                    'error': 'Impossibile eliminare l\'unico servizio dell\'ordine'
                }, status=400)

            # Salva i dati per il log
            servizio_nome = item.servizio_prodotto.titolo
            item_subtotale = item.subtotale

            # Elimina l'item
            item.delete()

            # Ricalcola i totali dell'ordine
            ordine.totale = sum(item.subtotale for item in ordine.items.all())

            # Ricalcola sconto se applicato
            if ordine.sconto_applicato:
                ordine.importo_sconto = ordine.sconto_applicato.calcola_sconto(ordine.totale)
            else:
                ordine.importo_sconto = 0

            ordine.totale_finale = ordine.totale - ordine.importo_sconto

            # Aggiorna stato pagamento
            ordine.aggiorna_stato_pagamento()
            ordine.save()

            # Log dell'operazione
            print(f"Item {item_id} ({servizio_nome}) eliminato dall'ordine {ordine.numero_progressivo} da {request.user}. Subtotale rimosso: €{item_subtotale}")

            return JsonResponse({
                'success': True,
                'message': f'Servizio "{servizio_nome}" eliminato con successo'
            })

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Errore durante l\'eliminazione: {str(e)}'
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    }, status=405)

@login_required
def segna_ritirata(request, pk):
    """Segna un ordine come ritirato dal cliente"""
    ordine = get_object_or_404(Ordine, pk=pk)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ritirata = data.get('ritirata', False)
            
            # Verifica che l'ordine sia completato
            if ordine.stato != 'completato':
                return JsonResponse({
                    'success': False,
                    'error': 'Solo gli ordini completati possono essere segnati come ritirati'
                }, status=400)
            
            # Aggiorna lo stato
            ordine.auto_ritirata = ritirata
            if ritirata:
                ordine.data_ritiro = timezone.now()
            else:
                ordine.data_ritiro = None
            
            ordine.save(update_fields=['auto_ritirata', 'data_ritiro'])
            
            # Notifica WebSocket
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                
                channel_layer = get_channel_layer()
                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        'ordini_list',
                        {
                            'type': 'ordine_modificato',
                            'ordine_id': ordine.id,
                            'numero_progressivo': ordine.numero_progressivo,
                            'timestamp': timezone.now().isoformat()
                        }
                    )
            except Exception as e:
                print(f'Errore WebSocket: {e}')
            
            return JsonResponse({
                'success': True,
                'message': 'Auto segnata come ritirata' if ritirata else 'Ritiro annullato',
                'data_ritiro': ordine.data_ritiro.strftime('%d/%m/%Y %H:%M') if ordine.data_ritiro else None
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Dati JSON non validi'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Metodo non consentito'
    }, status=405)


# ==================== PIANIFICAZIONE TIMELINE ====================

class PianificazioneView(LoginRequiredMixin, TemplateView):
    """Pianificazione ordini con timeline e durate variabili"""
    template_name = 'ordini/pianificazione.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Configurazione orari
        config = ConfigurazionePianificazione.get_configurazione()
        context['configurazione'] = config

        # Data selezionata
        data_str = self.request.GET.get('data')
        if data_str:
            try:
                data_selezionata = datetime.strptime(data_str, '%Y-%m-%d').date()
            except ValueError:
                data_selezionata = timezone.now().date()
        else:
            data_selezionata = timezone.now().date()

        context['data_selezionata'] = data_selezionata
        context['data_precedente'] = data_selezionata - timedelta(days=1)
        context['data_successiva'] = data_selezionata + timedelta(days=1)

        # Orari timeline
        ora_inizio = timezone.make_aware(
            datetime.combine(data_selezionata, config.ora_inizio)
        )
        ora_fine = timezone.make_aware(
            datetime.combine(data_selezionata, config.ora_fine)
        )
        context['ora_inizio_timeline'] = ora_inizio
        context['ora_fine_timeline'] = ora_fine

        # Carica ordini non completati
        ordini = Ordine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione']
        ).select_related('cliente').prefetch_related(
            'items__servizio_prodotto'
        ).order_by('data_ora')

        # Aggiorna durata stimata per ordini che non l'hanno
        for ordine in ordini:
            if ordine.durata_stimata_minuti == 0:
                ordine.aggiorna_durata_stimata()

        # Separa ordini pianificati per questa data da non pianificati
        ordini_pianificati = ordini.filter(
            ora_consegna_prevista__isnull=False,
            ora_consegna_prevista__date=data_selezionata,
            ora_consegna_prevista__gte=ora_inizio,
            ora_consegna_prevista__lte=ora_fine
        ).order_by('ora_consegna_prevista')

        ordini_non_pianificati = ordini.filter(
            Q(ora_consegna_prevista__isnull=True) |
            ~Q(ora_consegna_prevista__date=data_selezionata) |
            Q(ora_consegna_prevista__lt=ora_inizio) |
            Q(ora_consegna_prevista__gt=ora_fine)
        ).order_by('data_ora')

        context['ordini_pianificati'] = ordini_pianificati
        context['ordini_non_pianificati'] = ordini_non_pianificati
        context['ora_corrente'] = timezone.now()

        # Calcola sovrapposizioni
        sovrapposizioni = self.calcola_sovrapposizioni(ordini_pianificati)
        context['sovrapposizioni'] = sovrapposizioni

        return context

    def calcola_sovrapposizioni(self, ordini):
        """Identifica ordini con sovrapposizioni temporali"""
        sovrapposizioni = []
        ordini_list = list(ordini)

        for i, ordine1 in enumerate(ordini_list):
            if not ordine1.ora_consegna_prevista or not ordine1.ora_fine_prevista:
                continue

            for ordine2 in ordini_list[i+1:]:
                if not ordine2.ora_consegna_prevista or not ordine2.ora_fine_prevista:
                    continue

                # Check sovrapposizione
                if (ordine1.ora_consegna_prevista < ordine2.ora_fine_prevista and
                    ordine1.ora_fine_prevista > ordine2.ora_consegna_prevista):
                    sovrapposizioni.append((ordine1.id, ordine2.id))

        return sovrapposizioni


@login_required
def assegna_ordine_timeline(request):
    """Assegna ordine a un orario specifico sulla timeline"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
        ordine_id = data.get('ordine_id')
        ora_consegna_str = data.get('ora_consegna')  # ISO format datetime string

        if not ordine_id or not ora_consegna_str:
            return JsonResponse({'error': 'Missing data'}, status=400)

        # Parse datetime
        ora_consegna = datetime.fromisoformat(ora_consegna_str.replace('Z', '+00:00'))
        if timezone.is_naive(ora_consegna):
            ora_consegna = timezone.make_aware(ora_consegna)

        # Validazione: non nel passato
        if ora_consegna < timezone.now():
            return JsonResponse({'error': 'Cannot schedule in past'}, status=400)

        # Get ordine
        ordine = get_object_or_404(Ordine, pk=ordine_id)

        # Validazione stato
        if ordine.stato not in ['in_attesa', 'in_lavorazione']:
            return JsonResponse({'error': 'Order already completed'}, status=400)

        # Assicurati che abbia durata stimata
        if ordine.durata_stimata_minuti == 0:
            ordine.aggiorna_durata_stimata()

        # Calcola ora fine prevista
        ora_fine_prevista = ora_consegna + timedelta(minutes=ordine.durata_stimata_minuti)

        # Verifica sovrapposizioni con altri ordini nella stessa data
        ordini_nella_data = Ordine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione'],
            ora_consegna_prevista__isnull=False,
            ora_consegna_prevista__date=ora_consegna.date()
        ).exclude(pk=ordine.id)  # Escludi l'ordine corrente (caso spostamento)

        for altro_ordine in ordini_nella_data:
            if altro_ordine.ora_fine_prevista:
                # Check sovrapposizione
                if (ora_consegna < altro_ordine.ora_fine_prevista and
                    ora_fine_prevista > altro_ordine.ora_consegna_prevista):
                    return JsonResponse({
                        'error': f'Sovrapposizione con ordine #{altro_ordine.numero_breve}',
                        'conflitto': True,
                        'ordine_conflitto': altro_ordine.numero_breve
                    }, status=400)

        # Aggiorna ora consegna prevista
        ordine.ora_consegna_prevista = ora_consegna

        # Calcola tempo attesa
        tempo_attesa = (ora_consegna - timezone.now()).total_seconds() / 60
        ordine.tempo_attesa_minuti = max(0, int(tempo_attesa))

        ordine.save(update_fields=['ora_consegna_prevista', 'tempo_attesa_minuti'])

        return JsonResponse({
            'success': True,
            'ordine_id': ordine.id,
            'numero_progressivo': ordine.numero_progressivo,
            'ora_consegna': ora_consegna.isoformat(),
            'ora_fine': ordine.ora_fine_prevista.isoformat() if ordine.ora_fine_prevista else None,
            'durata_minuti': ordine.durata_stimata_minuti
        })

    except Exception as e:
        print(f"Error assigning order to timeline: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def rimuovi_ordine_timeline(request):
    """Rimuove ordine dalla pianificazione"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
        ordine_id = data.get('ordine_id')

        ordine = get_object_or_404(Ordine, pk=ordine_id)
        ordine.ora_consegna_prevista = None
        ordine.tempo_attesa_minuti = 0
        ordine.save(update_fields=['ora_consegna_prevista', 'tempo_attesa_minuti'])

        return JsonResponse({'success': True, 'ordine_id': ordine.id})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def modifica_durata_ordine(request, pk):
    """Modifica manualmente la durata stimata di un ordine"""
    ordine = get_object_or_404(Ordine, pk=pk)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            nuova_durata = data.get('durata_minuti')

            if nuova_durata is None or nuova_durata < 0:
                return JsonResponse({'error': 'Invalid duration'}, status=400)

            ordine.durata_stimata_minuti = int(nuova_durata)
            ordine.durata_modificata_manualmente = True
            ordine.save(update_fields=['durata_stimata_minuti', 'durata_modificata_manualmente'])

            return JsonResponse({
                'success': True,
                'ordine_id': ordine.id,
                'durata_minuti': ordine.durata_stimata_minuti,
                'ora_fine': ordine.ora_fine_prevista.isoformat() if ordine.ora_fine_prevista else None
            })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def ricalcola_durata_ordine(request, pk):
    """Ricalcola durata da servizi (reset override manuale)"""
    ordine = get_object_or_404(Ordine, pk=pk)

    if request.method == 'POST':
        try:
            ordine.aggiorna_durata_stimata(forza_ricalcolo=True)

            return JsonResponse({
                'success': True,
                'ordine_id': ordine.id,
                'durata_minuti': ordine.durata_stimata_minuti,
                'ora_fine': ordine.ora_fine_prevista.isoformat() if ordine.ora_fine_prevista else None
            })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


class ConfigurazionePianificazioneUpdateView(LoginRequiredMixin, UpdateView):
    """Modifica orari di lavoro"""
    model = ConfigurazionePianificazione
    template_name = 'ordini/configurazione_pianificazione.html'
    fields = ['ora_inizio', 'ora_fine', 'pausa_pranzo_attiva',
              'ora_inizio_pausa', 'ora_fine_pausa']
    success_url = reverse_lazy('ordini:pianificazione')

    def get_object(self, queryset=None):
        return ConfigurazionePianificazione.get_configurazione()

    def form_valid(self, form):
        form.instance.aggiornato_da = self.request.user
        messages.success(self.request, 'Configurazione aggiornata')
        return super().form_valid(form)


@login_required
def aggiorna_priorita_ordini(request):
    """AJAX POST: aggiorna la priorità degli ordini dopo drag-and-drop."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo non consentito'}, status=405)
    try:
        data = json.loads(request.body)
        ordini_ids = data.get('ordini_ids', [])  # Lista di ID nell'ordine desiderato
        if not ordini_ids:
            return JsonResponse({'success': False, 'error': 'Nessun ordine specificato'})
        for idx, ordine_id in enumerate(ordini_ids):
            Ordine.objects.filter(pk=ordine_id).update(priorita=idx + 1)
        return JsonResponse({'success': True, 'count': len(ordini_ids)})
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
