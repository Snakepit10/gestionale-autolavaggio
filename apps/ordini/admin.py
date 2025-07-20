from django.contrib import admin
from .models import Ordine, ItemOrdine, Pagamento


@admin.register(Ordine)
class OrdineAdmin(admin.ModelAdmin):
    list_display = ['numero_progressivo', 'cliente', 'data_ora', 'stato', 'stato_pagamento', 'totale']
    list_filter = ['stato', 'stato_pagamento', 'origine', 'data_ora']
    search_fields = ['numero_progressivo', 'cliente__nome', 'cliente__cognome']
    readonly_fields = ['numero_progressivo', 'data_ora']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cliente')


@admin.register(ItemOrdine)
class ItemOrdineAdmin(admin.ModelAdmin):
    list_display = ['ordine', 'servizio_prodotto', 'quantita', 'prezzo_unitario', 'stato']
    list_filter = ['stato', 'servizio_prodotto__categoria']
    search_fields = ['ordine__numero_progressivo', 'servizio_prodotto__titolo']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('ordine', 'servizio_prodotto')


@admin.register(Pagamento)
class PagamentoAdmin(admin.ModelAdmin):
    list_display = ['ordine', 'metodo', 'importo', 'data_pagamento']
    list_filter = ['metodo', 'data_pagamento']
    search_fields = ['ordine__numero_progressivo', 'riferimento']
    readonly_fields = ['data_pagamento']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('ordine')