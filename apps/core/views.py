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
    Categoria, ServizioProdotto, Sconto, StampanteRete
)
from .forms import (
    CategoriaForm, ServizioProdottoForm, ScontoForm, StampanteReteForm
)


class HomeView(TemplateView):
    """Home dispatch:
    - Anonimo: redirect alla landing pubblica clienti
    - Cliente loggato (ha .cliente): redirect alla sua dashboard
    - Staff/admin: home dashboard staff esistente
    """
    template_name = 'core/home.html'

    def dispatch(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        if not request.user.is_authenticated:
            return redirect('clients:landing')
        if hasattr(request.user, 'cliente') and not (request.user.is_staff or request.user.is_superuser):
            return redirect('clients:dashboard')
        return super().dispatch(request, *args, **kwargs)

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
        from django.db.models import F
        from apps.auth_system.sezioni import ha_accesso
        from apps.magazzino.models import Articolo
        context['prodotti_scorta_bassa'] = Articolo.objects.filter(
            attivo=True, traccia_scorte=True, quantita__lte=F('scorta_minima'),
        ).count() if ha_accesso(self.request.user, 'magazzino') else 0
        
        # Ordini di oggi
        ordini_oggi = Ordine.objects.filter(
            data_ora__range=[inizio_oggi, fine_oggi]
        )
        context['ordini_oggi'] = ordini_oggi.filter(vendita_prodotti=False).count()
        
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

    def get_context_data(self, **kwargs):
        """Ogni categoria riceve `sottocategorie`: [(nome, n. item)] dal
        campo `gruppo` degli item che la hanno come categoria principale."""
        from django.db.models import Count

        context = super().get_context_data(**kwargs)
        righe = (ServizioProdotto.objects.exclude(gruppo='')
                 .values('categoria_id', 'gruppo', 'ordine_gruppo')
                 .annotate(n=Count('id')).order_by('ordine_gruppo', 'gruppo'))
        per_categoria = {}
        for r in righe:
            per_categoria.setdefault(r['categoria_id'], []).append((r['gruppo'], r['n']))
        for c in context['categorie']:
            c.sottocategorie = per_categoria.get(c.pk, [])
        return context


@login_required
def rinomina_sottocategoria(request, pk):
    """POST {vecchio, nuovo}: rinomina una sottocategoria su tutti gli item
    della categoria (nuovo vuoto = toglie la sottocategoria)."""
    import json

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo non consentito'}, status=405)
    categoria = get_object_or_404(Categoria, pk=pk)
    try:
        dati = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Dati non validi'}, status=400)
    vecchio = (dati.get('vecchio') or '').strip()
    nuovo = (dati.get('nuovo') or '').strip()[:100]
    if not vecchio:
        return JsonResponse({'success': False, 'error': 'Sottocategoria non indicata'}, status=400)
    n = ServizioProdotto.objects.filter(categoria=categoria, gruppo=vecchio).update(gruppo=nuovo)
    return JsonResponse({'success': True, 'aggiornati': n})


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
    """API JSON per elenco servizi/prodotti attivi raggruppati per categoria"""
    servizi = ServizioProdotto.objects.filter(
        attivo=True
    ).select_related('categoria').order_by('categoria__ordine_visualizzazione', 'titolo')

    # Raggruppa per categoria
    categorie_data = {}
    for servizio in servizi:
        categoria_nome = servizio.categoria.nome if servizio.categoria else 'Senza categoria'
        if categoria_nome not in categorie_data:
            categorie_data[categoria_nome] = []

        categorie_data[categoria_nome].append({
            'id': servizio.id,
            'titolo': servizio.titolo,
            'prezzo': float(servizio.prezzo),
            'tipo': servizio.tipo,
            'durata_minuti': servizio.durata_minuti or 0,
            'disponibile': servizio.disponibile,
            'quantita_disponibile': servizio.quantita_disponibile if servizio.tipo == 'prodotto' else None
        })

    return JsonResponse({'categorie': categorie_data})


@login_required
def servizi_api(request):
    """API per ottenere la lista dei servizi/prodotti disponibili"""
    servizi = ServizioProdotto.objects.filter(attivo=True).select_related("categoria").order_by("categoria__ordine_visualizzazione", "titolo")
    data = {}
    for servizio in servizi:
        categoria_nome = servizio.categoria.nome
        if categoria_nome not in data:
            data[categoria_nome] = []
        data[categoria_nome].append({
            "id": servizio.id,
            "titolo": servizio.titolo,
            "prezzo": float(servizio.prezzo),
            "tipo": servizio.tipo,
            "disponibile": servizio.disponibile,
            "quantita_disponibile": servizio.quantita_disponibile if servizio.tipo == "prodotto" else None
        })
    return JsonResponse(data, safe=False)


@login_required
def toggle_mostra_pubblico(request, pk):
    """AJAX: flip ServizioProdotto.mostra_pubblico.

    Solo staff puo togliere/mettere il flag.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Non autorizzato'}, status=403)
    try:
        sp = ServizioProdotto.objects.get(pk=pk)
    except ServizioProdotto.DoesNotExist:
        return JsonResponse({'error': 'Servizio non trovato'}, status=404)
    sp.mostra_pubblico = not sp.mostra_pubblico
    sp.save(update_fields=['mostra_pubblico'])
    return JsonResponse({'ok': True, 'mostra_pubblico': sp.mostra_pubblico})


@login_required
def duplica_servizio(request, pk):
    """Duplica un ServizioProdotto.

    Crea una copia con titolo "Copia di <originale>" e redirige al form
    di modifica del duplicato cosi' l'operatore puo' rifinire i campi
    (titolo, prezzo, ecc.) prima di salvare definitivamente. Copia
    anche le relazioni M2M (postazioni, categorie_aggiuntive, upsell_per)
    e tutti i flag/attributi del modello.
    """
    from django.contrib import messages
    from django.shortcuts import get_object_or_404, redirect
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Non autorizzato.')
        return redirect('core:catalogo-list')

    originale = get_object_or_404(ServizioProdotto, pk=pk)
    # Snapshot delle M2M prima del clone: dopo aver azzerato il pk
    # l'istanza perde l'accesso alle vecchie relazioni; le riapplichiamo
    # al duplicato dopo il primo save().
    postazioni_ids = list(originale.postazioni.values_list('id', flat=True))
    cat_aggiuntive_ids = list(originale.categorie_aggiuntive.values_list('id', flat=True))
    upsell_per_ids = list(originale.upsell_per.values_list('id', flat=True))

    # Clone: pk=None forza un INSERT al prossimo save() invece dell'UPDATE.
    originale.pk = None
    originale.id = None
    originale.titolo = f'Copia di {originale.titolo}'[:200]
    originale.save()

    if postazioni_ids:
        originale.postazioni.set(postazioni_ids)
    if cat_aggiuntive_ids:
        originale.categorie_aggiuntive.set(cat_aggiuntive_ids)
    if upsell_per_ids:
        originale.upsell_per.set(upsell_per_ids)

    messages.success(
        request,
        f'Duplicato creato: "{originale.titolo}". Modifica i campi e salva.'
    )
    return redirect('core:catalogo-update', pk=originale.pk)

