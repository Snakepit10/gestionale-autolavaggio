from django.contrib import admin
from .models import (
    Categoria, Postazione, ServizioProdotto, Sconto, 
    StampanteRete, MovimentoScorte
)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'ordine_visualizzazione', 'attiva']
    list_editable = ['ordine_visualizzazione', 'attiva']
    ordering = ['ordine_visualizzazione', 'nome']


@admin.register(Postazione)
class PostazioneAdmin(admin.ModelAdmin):
    list_display = ['nome', 'ordine_visualizzazione', 'attiva', 'stampante_comande']
    list_editable = ['ordine_visualizzazione', 'attiva']
    ordering = ['ordine_visualizzazione']


@admin.register(ServizioProdotto)
class ServizioProdottoAdmin(admin.ModelAdmin):
    list_display = ['titolo', 'tipo', 'categoria', 'prezzo', 'attivo', 'scorta_bassa']
    list_filter = ['tipo', 'categoria', 'attivo']
    search_fields = ['titolo', 'descrizione', 'codice_prodotto']
    filter_horizontal = ['postazioni']
    
    def scorta_bassa(self, obj):
        return obj.scorta_bassa
    scorta_bassa.boolean = True
    scorta_bassa.short_description = 'Scorta Bassa'


@admin.register(Sconto)
class ScontoAdmin(admin.ModelAdmin):
    list_display = ['titolo', 'tipo_sconto', 'valore', 'attivo']
    list_filter = ['tipo_sconto', 'attivo']


@admin.register(StampanteRete)
class StampanteReteAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'indirizzo_ip', 'porta', 'attiva', 'predefinita']
    list_filter = ['tipo', 'attiva', 'predefinita']


@admin.register(MovimentoScorte)
class MovimentoScorteAdmin(admin.ModelAdmin):
    list_display = ['prodotto', 'tipo', 'quantita', 'data_movimento', 'operatore']
    list_filter = ['tipo', 'data_movimento']
    readonly_fields = ['quantita_prima', 'quantita_dopo', 'data_movimento']