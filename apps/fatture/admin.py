from django.contrib import admin

from .models import Fattura, RigaFattura


class RigaFatturaInline(admin.TabularInline):
    model = RigaFattura
    extra = 0


@admin.register(Fattura)
class FatturaAdmin(admin.ModelAdmin):
    list_display = ['numero', 'data', 'ragione_sociale', 'cliente', 'stato']
    list_filter = ['stato', 'data']
    search_fields = ['numero', 'ragione_sociale']
    date_hierarchy = 'data'
    inlines = [RigaFatturaInline]
