from django.contrib import admin

from .models import (ContoFinanziario, FatturaPassiva, MessaggioPec, MovimentoFinanziario, RegolaFornitore,
                     RegolaMovimento, RigaFatturaPassiva, ScadenzaFattura, VoceConto)


@admin.register(VoceConto)
class VoceContoAdmin(admin.ModelAdmin):
    list_display = ['codice', 'nome', 'sezione', 'flusso', 'attiva']
    list_filter = ['sezione', 'flusso', 'attiva']


class RigaInline(admin.TabularInline):
    model = RigaFatturaPassiva
    extra = 0


class ScadenzaInline(admin.TabularInline):
    model = ScadenzaFattura
    extra = 0


@admin.register(FatturaPassiva)
class FatturaPassivaAdmin(admin.ModelAdmin):
    list_display = ['data', 'fornitore', 'numero', 'tipo_documento', 'imponibile', 'totale', 'origine', 'classificata']
    list_filter = ['origine', 'classificata', 'tipo_documento']
    search_fields = ['numero', 'fornitore__ragione_sociale']
    inlines = [RigaInline, ScadenzaInline]


admin.site.register(RegolaFornitore)
admin.site.register(MessaggioPec)


@admin.register(ContoFinanziario)
class ContoFinanziarioAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'saldo_iniziale', 'data_saldo_iniziale', 'attivo']


@admin.register(MovimentoFinanziario)
class MovimentoFinanziarioAdmin(admin.ModelAdmin):
    list_display = ['data', 'conto', 'importo', 'causale', 'voce', 'origine']
    list_filter = ['conto', 'origine', 'voce']
    search_fields = ['descrizione', 'causale']


@admin.register(RegolaMovimento)
class RegolaMovimentoAdmin(admin.ModelAdmin):
    list_display = ['ordine', 'testo', 'segno', 'conto', 'voce']
