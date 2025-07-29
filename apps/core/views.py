from django.shortcuts import render, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import (
    TemplateView, ListView, CreateView, UpdateView, DeleteView
)
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.contrib import messages
from .models import (
    Categoria, ServizioProdotto, Sconto, StampanteRete, MovimentoScorte
)
from .forms import (
    CategoriaForm, ServizioProdottoForm, ScontoForm, StampanteReteForm
)


class HomeView(TemplateView):  # Temporaneamente rimosso LoginRequiredMixin
    template_name = 'core/home.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from datetime import date, datetime, timedelta
        from django.db.models import Sum, Count, Q
        from apps.ordini.models import Ordine
        from apps.clienti.models import Cliente
        
        oggi = date.today()
        inizio_oggi = datetime.combine(oggi, datetime.min.time())
        fine_oggi = datetime.combine(oggi, datetime.max.time())
        
        # Statistiche dashboard
        context['prodotti_scorta_bassa'] = ServizioProdotto.objects.filter(
            tipo='prodotto',
            quantita_disponibile__gt=0
        ).extra(
            where=["quantita_disponibile <= quantita_minima_alert"]
        ).count()
        
        # Ordini di oggi
        ordini_oggi = Ordine.objects.filter(
            data_ora__range=[inizio_oggi, fine_oggi]
        )
        context['ordini_oggi'] = ordini_oggi.count()
        
        # Incasso di oggi
        incasso_oggi = ordini_oggi.filter(
            stato_pagamento='pagato'
        ).aggregate(totale=Sum('totale_finale'))['totale'] or 0
        context['incasso_oggi'] = incasso_oggi
        
        # Ordini in lavorazione
        context['ordini_in_lavorazione'] = Ordine.objects.filter(
            stato='in_lavorazione'
        ).count()
        
        # Totale clienti
        context['clienti_totali'] = Cliente.objects.count()
        
        # Ultimi 5 ordini
        context['ordini_recenti'] = Ordine.objects.select_related(
            'cliente'
        ).order_by('-data_ora')[:5]
        
        # Stato postazioni (se disponibile)
        try:
            from apps.postazioni.models import Postazione
            postazioni = Postazione.objects.all()
            context['postazioni'] = postazioni
            context['postazioni_attive'] = postazioni.filter(attiva=True).count()
            context['postazioni_totali'] = postazioni.count()
        except:
            context['postazioni'] = []
            context['postazioni_attive'] = 0
            context['postazioni_totali'] = 0
        
        # Statistiche settimanali per il grafico
        sette_giorni_fa = oggi - timedelta(days=7)
        ordini_settimana = []
        for i in range(7):
            giorno = sette_giorni_fa + timedelta(days=i)
            inizio_giorno = datetime.combine(giorno, datetime.min.time())
            fine_giorno = datetime.combine(giorno, datetime.max.time())
            
            count = Ordine.objects.filter(
                data_ora__range=[inizio_giorno, fine_giorno]
            ).count()
            
            ordini_settimana.append({
                'giorno': giorno.strftime('%d/%m'),
                'count': count
            })
        
        import json
        context['ordini_settimana'] = json.dumps(ordini_settimana)
        
        return context


# CRUD Categorie
class CategoriaListView(LoginRequiredMixin, ListView):
    model = Categoria
    template_name = 'core/categoria_list.html'
    context_object_name = 'categorie'


class CategoriaCreateView(LoginRequiredMixin, CreateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = 'core/categoria_form.html'
    success_url = reverse_lazy('core:categoria-list')


class CategoriaUpdateView(LoginRequiredMixin, UpdateView):
    model = Categoria
    form_class = CategoriaForm
    template_name = 'core/categoria_form.html'
    success_url = reverse_lazy('core:categoria-list')


class CategoriaDeleteView(LoginRequiredMixin, DeleteView):
    model = Categoria
    template_name = 'core/categoria_confirm_delete.html'
    success_url = reverse_lazy('core:categoria-list')


# CRUD Servizi/Prodotti
class CatalogoListView(LoginRequiredMixin, ListView):
    model = ServizioProdotto
    template_name = 'core/catalogo_list.html'
    context_object_name = 'servizi_prodotti'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = ServizioProdotto.objects.select_related('categoria')
        
        # Filtri
        categoria = self.request.GET.get('categoria')
        tipo = self.request.GET.get('tipo')
        stato = self.request.GET.get('stato')
        search = self.request.GET.get('search')
        
        if categoria:
            queryset = queryset.filter(categoria__nome=categoria)
        if tipo:
            queryset = queryset.filter(tipo=tipo)
        if stato == 'attivo':
            queryset = queryset.filter(attivo=True)
        elif stato == 'inattivo':
            queryset = queryset.filter(attivo=False)
        if search:
            queryset = queryset.filter(titolo__icontains=search)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categorie'] = Categoria.objects.filter(attiva=True)
        return context


class CatalogoCreateView(LoginRequiredMixin, CreateView):
    model = ServizioProdotto
    form_class = ServizioProdottoForm
    template_name = 'core/catalogo_form.html'
    success_url = reverse_lazy('core:catalogo-list')


class CatalogoUpdateView(LoginRequiredMixin, UpdateView):
    model = ServizioProdotto
    form_class = ServizioProdottoForm
    template_name = 'core/catalogo_form.html'
    success_url = reverse_lazy('core:catalogo-list')


class CatalogoDeleteView(LoginRequiredMixin, DeleteView):
    model = ServizioProdotto
    template_name = 'core/catalogo_confirm_delete.html'
    success_url = reverse_lazy('core:catalogo-list')


# CRUD Sconti
class ScontiListView(LoginRequiredMixin, ListView):
    model = Sconto
    template_name = 'core/sconti_list.html'
    context_object_name = 'sconti'


class ScontoCreateView(LoginRequiredMixin, CreateView):
    model = Sconto
    form_class = ScontoForm
    template_name = 'core/sconto_form.html'
    success_url = reverse_lazy('core:sconti-list')


class ScontoUpdateView(LoginRequiredMixin, UpdateView):
    model = Sconto
    form_class = ScontoForm
    template_name = 'core/sconto_form.html'
    success_url = reverse_lazy('core:sconti-list')


class ScontoDeleteView(LoginRequiredMixin, DeleteView):
    model = Sconto
    template_name = 'core/sconto_confirm_delete.html'
    success_url = reverse_lazy('core:sconti-list')


# Configurazione Stampanti
class StampantiListView(LoginRequiredMixin, ListView):
    model = StampanteRete
    template_name = 'core/stampanti_list.html'
    context_object_name = 'stampanti'


class StampanteCreateView(LoginRequiredMixin, CreateView):
    model = StampanteRete
    form_class = StampanteReteForm
    template_name = 'core/stampante_form.html'
    success_url = reverse_lazy('core:stampanti-list')


class StampanteUpdateView(LoginRequiredMixin, UpdateView):
    model = StampanteRete
    form_class = StampanteReteForm
    template_name = 'core/stampante_form.html'
    success_url = reverse_lazy('core:stampanti-list')


# Gestione Scorte
class ScorteListView(LoginRequiredMixin, ListView):
    model = ServizioProdotto
    template_name = 'core/scorte_list.html'
    context_object_name = 'prodotti'
    
    def get_queryset(self):
        return ServizioProdotto.objects.filter(tipo='prodotto', attivo=True)


class MovimentiScorteView(LoginRequiredMixin, ListView):
    model = MovimentoScorte
    template_name = 'core/movimenti_scorte_list.html'
    context_object_name = 'movimenti'
    paginate_by = 50


class ProdottiSottoScortaView(LoginRequiredMixin, ListView):
    model = ServizioProdotto
    template_name = 'core/alert_scorte.html'
    context_object_name = 'prodotti_sotto_scorta'
    
    def get_queryset(self):
        return ServizioProdotto.objects.filter(
            tipo='prodotto',
            quantita_disponibile__gt=0,
            attivo=True
        ).extra(
            where=["quantita_disponibile <= quantita_minima_alert"]
        )


@login_required
def test_stampante(request, pk):
    """Test di connessione con una stampante"""
    stampante = get_object_or_404(StampanteRete, pk=pk)
    
    try:
        # Qui implementare il test di connessione reale
        # Per ora simuliamo
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((stampante.indirizzo_ip, stampante.porta))
        sock.close()
        
        if result == 0:
            messages.success(request, f'Connessione con {stampante.nome} riuscita!')
        else:
            messages.error(request, f'Impossibile connettersi a {stampante.nome}')
            
    except Exception as e:
        messages.error(request, f'Errore durante il test: {str(e)}')
    
    return JsonResponse({'success': result == 0})


@login_required
def servizi_json(request):
    """API JSON per elenco servizi attivi"""
    servizi = ServizioProdotto.objects.filter(
        tipo='servizio',
        attivo=True
    ).order_by('titolo')
    
    servizi_data = []
    for servizio in servizi:
        servizi_data.append({
            'id': servizio.id,
            'titolo': servizio.titolo,
            'prezzo': float(servizio.prezzo),
            'durata_minuti': servizio.durata_minuti or 0,
            'categoria': servizio.categoria.nome if servizio.categoria else None
        })
    
    return JsonResponse({'servizi': servizi_data})


@login_required
def movimento_scorte(request):
    """Gestisce i movimenti di scorte (carico/scarico)"""
    if request.method == 'POST':
        from django.shortcuts import redirect
        from django.db import transaction
        
        prodotto_id = request.POST.get('prodotto_id')
        tipo = request.POST.get('tipo')  # 'carico' o 'scarico'
        quantita_str = request.POST.get('quantita', '0')
        note = request.POST.get('note', '')
        
        # Debug
        print(f"DEBUG: prodotto_id={prodotto_id}, tipo={tipo}, quantita_str={quantita_str}, note={note}")
        
        try:
            quantita = int(quantita_str)
        except (ValueError, TypeError):
            quantita = 0
        
        if prodotto_id and tipo and quantita > 0:
            try:
                with transaction.atomic():
                    prodotto = get_object_or_404(ServizioProdotto, id=prodotto_id)
                    
                    # Cattura la quantità prima del movimento
                    quantita_prima = prodotto.quantita_disponibile
                    
                    # Calcola la quantità da usare nel movimento (con segno appropriato)
                    if tipo == 'carico':
                        quantita_movimento = quantita  # Positivo per carico
                        quantita_dopo = quantita_prima + quantita
                    elif tipo == 'scarico':
                        if quantita_prima >= quantita:
                            quantita_movimento = -quantita  # Negativo per scarico
                            quantita_dopo = quantita_prima - quantita
                        else:
                            messages.error(request, f'Quantità insufficiente per scarico. Disponibili: {quantita_prima}, Richieste: {quantita}')
                            return redirect('core:scorte-list')
                    else:
                        messages.error(request, 'Tipo di movimento non valido.')
                        return redirect('core:scorte-list')
                    
                    # Crea il movimento con tutti i campi richiesti
                    movimento = MovimentoScorte.objects.create(
                        prodotto=prodotto,
                        tipo=tipo,
                        quantita=quantita_movimento,
                        quantita_prima=quantita_prima,
                        quantita_dopo=quantita_dopo,
                        nota=note,
                        operatore=request.user
                    )
                    
                    # Aggiorna la quantità disponibile del prodotto
                    prodotto.quantita_disponibile = quantita_dopo
                    prodotto.save()
                    
                    # Messaggio di successo
                    tipo_display = 'Carico' if tipo == 'carico' else 'Scarico'
                    messages.success(request, 
                        f'{tipo_display} di {abs(quantita_movimento)} unità per {prodotto.titolo} registrato con successo. '
                        f'Quantità attuale: {quantita_dopo}')
                
            except Exception as e:
                messages.error(request, f'Errore durante il movimento: {str(e)}')
                print(f"DEBUG: Errore movimento: {str(e)}")
        else:
            messages.error(request, f'Dati incompleti per il movimento. prodotto_id={prodotto_id}, tipo={tipo}, quantita={quantita}')
            print(f"DEBUG: Dati incompleti - prodotto_id={prodotto_id}, tipo={tipo}, quantita={quantita}")
    else:
        messages.error(request, 'Metodo non consentito.')
        print(f"DEBUG: Metodo non POST: {request.method}")
    
    return redirect('core:scorte-list')