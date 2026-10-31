from django.contrib import admin

from .models import (FatturaPassiva, MessaggioPec, RegolaFornitore, RigaFatturaPassiva,
                     ScadenzaFattura, VoceConto)


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
