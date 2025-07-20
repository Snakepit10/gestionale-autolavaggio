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
        
        # Statistiche dashboard
        context['prodotti_scorta_bassa'] = ServizioProdotto.objects.filter(
            tipo='prodotto',
            quantita_disponibile__gt=0
        ).extra(
            where=["quantita_disponibile <= quantita_minima_alert"]
        ).count()
        
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
        search = self.request.GET.get('search')
        
        if categoria:
            queryset = queryset.filter(categoria_id=categoria)
        if tipo:
            queryset = queryset.filter(tipo=tipo)
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