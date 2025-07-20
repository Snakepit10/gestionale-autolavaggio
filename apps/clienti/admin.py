from django.contrib import admin
from .models import Cliente, PuntiFedelta, MovimentoPunti


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'tipo', 'email', 'telefono', 'data_registrazione', 'ordini_count', 'has_account']
    list_filter = ['tipo', 'consenso_marketing', 'data_registrazione']
    search_fields = ['nome', 'cognome', 'ragione_sociale', 'email', 'telefono', 'partita_iva', 'codice_fiscale']
    readonly_fields = ['data_registrazione']
    
    fieldsets = (
        ('Informazioni Generali', {
            'fields': ('tipo', 'email', 'telefono', 'indirizzo', 'cap', 'citta')
        }),
        ('Dati Privato', {
            'fields': ('nome', 'cognome', 'codice_fiscale'),
            'classes': ('collapse',)
        }),
        ('Dati Azienda', {
            'fields': ('ragione_sociale', 'partita_iva', 'codice_sdi', 'pec'),
            'classes': ('collapse',)
        }),
        ('Account Online', {
            'fields': ('user', 'consenso_marketing', 'data_registrazione')
        })
    )
    
    def ordini_count(self, obj):
        return obj.ordine_set.count()
    ordini_count.short_description = 'Ordini'
    
    def has_account(self, obj):
        return bool(obj.user)
    has_account.boolean = True
    has_account.short_description = 'Account Online'


@admin.register(PuntiFedelta)
class PuntiFedeltaAdmin(admin.ModelAdmin):
    list_display = ['cliente', 'punti_totali', 'punti_utilizzati', 'punti_disponibili']
    list_filter = ['punti_totali', 'punti_utilizzati']
    search_fields = ['cliente__nome', 'cliente__cognome', 'cliente__ragione_sociale']
    readonly_fields = ['punti_disponibili']


@admin.register(MovimentoPunti)
class MovimentoPuntiAdmin(admin.ModelAdmin):
    list_display = ['cliente', 'tipo', 'punti', 'descrizione', 'data_movimento']
    list_filter = ['tipo', 'data_movimento']
    search_fields = ['cliente__nome', 'cliente__cognome', 'descrizione']
    readonly_fields = ['data_movimento']