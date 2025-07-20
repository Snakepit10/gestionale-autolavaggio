from django.contrib import admin
from .models import ReportPersonalizzato, EsecuzioneReport, Dashboard, KPI, StoricoCambiamenti


@admin.register(ReportPersonalizzato)
class ReportPersonalizzatoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'creato_da', 'data_creazione', 'invio_automatico', 'attivo']
    list_filter = ['tipo', 'invio_automatico', 'attivo', 'data_creazione']
    search_fields = ['nome', 'descrizione']
    readonly_fields = ['data_creazione']
    
    fieldsets = (
        ('Informazioni Generali', {
            'fields': ('nome', 'descrizione', 'tipo', 'creato_da')
        }),
        ('Configurazione', {
            'fields': ('periodo_default', 'formato_default', 'filtri_custom', 'colonne_visibili', 'grafici_inclusi')
        }),
        ('Invio Automatico', {
            'fields': ('invio_automatico', 'frequenza_invio', 'email_destinatari', 'prossimo_invio')
        }),
        ('Stato', {
            'fields': ('attivo', 'data_creazione')
        })
    )


@admin.register(EsecuzioneReport)
class EsecuzioneReportAdmin(admin.ModelAdmin):
    list_display = ['report', 'eseguito_da', 'data_esecuzione', 'stato', 'tempo_esecuzione_secondi']
    list_filter = ['stato', 'data_esecuzione', 'report__tipo']
    search_fields = ['report__nome', 'eseguito_da__username']
    readonly_fields = ['data_esecuzione', 'data_completamento', 'tempo_esecuzione_secondi', 'righe_elaborate']
    
    fieldsets = (
        ('Esecuzione', {
            'fields': ('report', 'eseguito_da', 'data_esecuzione', 'data_completamento')
        }),
        ('Stato', {
            'fields': ('stato', 'messaggio_errore')
        }),
        ('Parametri', {
            'fields': ('parametri_esecuzione',)
        }),
        ('Output', {
            'fields': ('file_output', 'dimensione_file')
        }),
        ('Statistiche', {
            'fields': ('tempo_esecuzione_secondi', 'righe_elaborate')
        })
    )


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo_dashboard', 'creato_da', 'data_creazione', 'pubblico', 'attivo']
    list_filter = ['tipo_dashboard', 'pubblico', 'attivo', 'data_creazione']
    search_fields = ['nome', 'descrizione']
    readonly_fields = ['data_creazione', 'data_modifica']
    filter_horizontal = ['utenti_autorizzati']
    
    fieldsets = (
        ('Informazioni Generali', {
            'fields': ('nome', 'descrizione', 'tipo_dashboard', 'creato_da')
        }),
        ('Configurazione', {
            'fields': ('layout_configurazione', 'widget_configurazione')
        }),
        ('Permessi', {
            'fields': ('pubblico', 'utenti_autorizzati')
        }),
        ('Stato', {
            'fields': ('attivo', 'data_creazione', 'data_modifica')
        })
    )


@admin.register(KPI)
class KPIAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'valore_corrente', 'tendenza', 'ultimo_aggiornamento', 'attivo']
    list_filter = ['tipo', 'periodo_calcolo', 'tendenza', 'attivo']
    search_fields = ['nome', 'descrizione']
    readonly_fields = ['ultimo_aggiornamento', 'valore_corrente', 'valore_precedente', 'tendenza']
    
    fieldsets = (
        ('Informazioni Generali', {
            'fields': ('nome', 'descrizione', 'tipo')
        }),
        ('Configurazione Calcolo', {
            'fields': ('formula_calcolo', 'periodo_calcolo')
        }),
        ('Visualizzazione', {
            'fields': ('unita_misura', 'formato_numero')
        }),
        ('Soglie Allarme', {
            'fields': ('soglia_minima', 'soglia_massima')
        }),
        ('Valori Correnti', {
            'fields': ('valore_corrente', 'valore_precedente', 'tendenza', 'ultimo_aggiornamento', 'prossimo_aggiornamento')
        }),
        ('Stato', {
            'fields': ('attivo', 'visibile_dashboard')
        })
    )


@admin.register(StoricoCambiamenti)
class StoricoCambiamentiAdmin(admin.ModelAdmin):
    list_display = ['utente', 'tipo_operazione', 'modello_interessato', 'data_operazione']
    list_filter = ['tipo_operazione', 'modello_interessato', 'data_operazione']
    search_fields = ['utente__username', 'descrizione', 'modello_interessato']
    readonly_fields = ['data_operazione']
    
    fieldsets = (
        ('Operazione', {
            'fields': ('utente', 'data_operazione', 'tipo_operazione')
        }),
        ('Oggetto', {
            'fields': ('modello_interessato', 'oggetto_id', 'descrizione')
        }),
        ('Dettagli', {
            'fields': ('dettagli_json',)
        }),
        ('Informazioni Tecniche', {
            'fields': ('ip_address', 'user_agent')
        })
    )
    
    def has_add_permission(self, request):
        # Non permettere aggiunta manuale
        return False
    
    def has_change_permission(self, request, obj=None):
        # Non permettere modifica
        return False