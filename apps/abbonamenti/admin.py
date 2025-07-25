from django.contrib import admin
from .models import (
    ConfigurazioneAbbonamento, Abbonamento, AccessoAbbonamento,
    ServizioInclusoAbbonamento, TargaAbbonamento
)


class ServizioInclusoAbbonamentoInline(admin.TabularInline):
    model = ServizioInclusoAbbonamento
    extra = 1
    fields = [
        'servizio', 
        'quantita_inclusa', 
        'accessi_totali_periodo', 
        'accessi_per_sottoperiodo', 
        'tipo_sottoperiodo'
    ]


@admin.register(ConfigurazioneAbbonamento)
class ConfigurazioneAbbonamentoAdmin(admin.ModelAdmin):
    list_display = ['titolo', 'giorni_durata', 'prezzo', 'attiva']
    list_filter = ['attiva', 'durata', 'modalita_targa']
    search_fields = ['titolo', 'descrizione']
    inlines = [ServizioInclusoAbbonamentoInline]


class TargaAbbonamentoInline(admin.TabularInline):
    model = TargaAbbonamento
    extra = 0
    fields = ['targa', 'attiva']


@admin.register(Abbonamento)
class AbbonamentoAdmin(admin.ModelAdmin):
    list_display = ['codice_accesso', 'cliente', 'configurazione', 'data_attivazione', 'data_scadenza', 'stato']
    list_filter = ['stato', 'configurazione', 'data_attivazione']
    search_fields = ['codice_accesso', 'cliente__nome', 'cliente__cognome']
    readonly_fields = ['codice_accesso', 'data_attivazione']
    inlines = [TargaAbbonamentoInline]


@admin.register(AccessoAbbonamento)
class AccessoAbbonamentoAdmin(admin.ModelAdmin):
    list_display = ['abbonamento', 'data_ora', 'autorizzato']
    list_filter = ['autorizzato', 'data_ora']
    search_fields = ['abbonamento__codice_accesso', 'abbonamento__cliente__nome']
    readonly_fields = ['data_ora']
    
    def has_add_permission(self, request):
        return False  # Gli accessi vengono creati automaticamente


@admin.register(ServizioInclusoAbbonamento)
class ServizioInclusoAbbonamentoAdmin(admin.ModelAdmin):
    list_display = [
        'configurazione', 
        'servizio', 
        'quantita_inclusa', 
        'accessi_totali_periodo',
        'accessi_per_sottoperiodo',
        'tipo_sottoperiodo'
    ]
    list_filter = ['configurazione', 'servizio', 'tipo_sottoperiodo']
    search_fields = ['configurazione__titolo', 'servizio__titolo']