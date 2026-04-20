from django.contrib import admin
from django.utils.html import format_html
from .models import ChiusuraCassa, MovimentoCassa, Cassa, ChiusuraCassaAutomatica, QuadraturaGiornaliera


@admin.register(QuadraturaGiornaliera)
class QuadraturaGiornalieraAdmin(admin.ModelAdmin):
    list_display = ['data', 'contanti_totali', 'lettore_carte_servito', 'fondo_cassa_iniziale', 'totale_reale_display', 'operatore', 'aggiornato_il']
    list_filter = ['data']
    readonly_fields = ['creato_il', 'aggiornato_il']
    date_hierarchy = 'data'

    def totale_reale_display(self, obj):
        return f"€{obj.totale_reale:.2f}"
    totale_reale_display.short_description = "Totale reale"


@admin.register(Cassa)
class CassaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'numero', 'tipo', 'tracking_washcycles', 'attiva', 'ordine']
    list_filter = ['tipo', 'attiva']
    list_editable = ['attiva', 'ordine']


@admin.register(ChiusuraCassaAutomatica)
class ChiusuraCassaAutomaticaAdmin(admin.ModelAdmin):
    list_display = ['data', 'cassa', 'incasso_totale', 'incasso_vendita_display',
                    'vendita_totale_display', 'wash_cycles', 'confermata']
    list_filter = ['data', 'cassa', 'confermata']
    readonly_fields = ['data_ora_chiusura', 'creato_il', 'aggiornato_il']

    def incasso_vendita_display(self, obj):
        return f"€{obj.incasso_vendita:.2f}"
    incasso_vendita_display.short_description = "Incasso vendita"

    def vendita_totale_display(self, obj):
        return f"€{obj.vendita_totale:.2f}"
    vendita_totale_display.short_description = "Vendita totale"


@admin.register(ChiusuraCassa)
class ChiusuraCassaAdmin(admin.ModelAdmin):
    list_display = [
        'data',
        'stato',
        'operatore_apertura',
        'fondo_cassa_iniziale',
        'totale_incassi_giornalieri_display',
        'cassa_teorica_display',
        'differenza_display',
        'confermata',
    ]
    list_filter = ['stato', 'confermata', 'data']
    search_fields = ['operatore_apertura__username', 'operatore_chiusura__username']
    readonly_fields = [
        'data_ora_apertura',
        'data_ora_chiusura',
        'totale_incassi_contanti',
        'totale_pagamenti_contanti',
        'totale_prelievi',
        'totale_versamenti',
        'totale_carte',
        'totale_bancomat',
        'totale_bonifici',
        'totale_altro',
        'cassa_teorica_finale',
        'differenza_cassa',
    ]
    fieldsets = (
        ('Apertura Cassa', {
            'fields': (
                'data',
                'operatore_apertura',
                'data_ora_apertura',
                'fondo_cassa_iniziale',
                'note_apertura',
            )
        }),
        ('Movimenti Giornalieri (Calcolati Automaticamente)', {
            'fields': (
                'totale_incassi_contanti',
                'totale_carte',
                'totale_bancomat',
                'totale_bonifici',
                'totale_altro',
                'totale_pagamenti_contanti',
                'totale_prelievi',
                'totale_versamenti',
            ),
            'classes': ('collapse',)
        }),
        ('Chiusura Cassa', {
            'fields': (
                'operatore_chiusura',
                'data_ora_chiusura',
                'cassa_teorica_finale',
                'conteggio_cassa_reale',
                'differenza_cassa',
                'note_chiusura',
            )
        }),
        ('Stato', {
            'fields': ('stato', 'confermata')
        }),
    )

    def totale_incassi_giornalieri_display(self, obj):
        return f"€{obj.totale_incassi_giornalieri:.2f}"
    totale_incassi_giornalieri_display.short_description = "Incassi Totali"

    def cassa_teorica_display(self, obj):
        return f"€{obj.cassa_teorica_finale:.2f}"
    cassa_teorica_display.short_description = "Cassa Teorica"

    def differenza_display(self, obj):
        diff = obj.differenza_cassa
        if diff is None:
            return "-"

        stato = obj.stato_differenza
        color = {
            'ok': 'green',
            'mancante': 'red',
            'eccedente': 'orange',
        }.get(stato, 'black')

        segno = '+' if diff > 0 else ''
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}{:.2f}€</span>',
            color,
            segno,
            diff
        )
    differenza_display.short_description = "Differenza"

    def has_delete_permission(self, request, obj=None):
        # Non permettere eliminazione se confermata
        if obj and obj.confermata:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        # Non permettere modifiche se confermata
        if obj and obj.confermata:
            return False
        return super().has_change_permission(request, obj)


@admin.register(MovimentoCassa)
class MovimentoCassaAdmin(admin.ModelAdmin):
    list_display = [
        'data_ora',
        'tipo',
        'categoria',
        'importo_display',
        'causale',
        'operatore',
        'chiusura_cassa',
    ]
    list_filter = ['tipo', 'categoria', 'data_ora', 'chiusura_cassa']
    search_fields = ['causale', 'dettagli', 'riferimento_documento']
    date_hierarchy = 'data_ora'
    fieldsets = (
        ('Informazioni Movimento', {
            'fields': (
                'chiusura_cassa',
                'data_ora',
                'tipo',
                'categoria',
                'importo',
            )
        }),
        ('Dettagli', {
            'fields': (
                'causale',
                'dettagli',
                'riferimento_documento',
                'operatore',
            )
        }),
    )

    def importo_display(self, obj):
        segno = '+' if obj.tipo in ['entrata', 'versamento'] else '-'
        color = 'green' if obj.tipo in ['entrata', 'versamento'] else 'red'
        return format_html(
            '<span style="color: {};">{}{:.2f}€</span>',
            color,
            segno,
            obj.importo
        )
    importo_display.short_description = "Importo"

    def has_delete_permission(self, request, obj=None):
        # Non permettere eliminazione se chiusura confermata
        if obj and obj.chiusura_cassa.confermata:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        # Non permettere modifiche se chiusura confermata
        if obj and obj.chiusura_cassa.confermata:
            return False
        return super().has_change_permission(request, obj)
