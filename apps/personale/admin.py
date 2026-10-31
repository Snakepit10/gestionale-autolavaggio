from django.contrib import admin

from .models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese


class ParametroInline(admin.TabularInline):
    model = ParametroRetributivo
    extra = 0


@admin.register(Dipendente)
class DipendenteAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'attivo', 'utente', 'ordine']
    list_filter = ['tipo', 'attivo']
    inlines = [ParametroInline]


class RigaInline(admin.TabularInline):
    model = RigaMese
    extra = 0


@admin.register(MesePersonale)
class MesePersonaleAdmin(admin.ModelAdmin):
    list_display = ['mese', 'ore_giorno_ferie', 'ore_giorno_malattia', 'aggiornato_il', 'aggiornato_da']
    inlines = [RigaInline]


@admin.register(Presenza)
class PresenzaAdmin(admin.ModelAdmin):
    list_display = ['dipendente', 'data', 'ore', 'assenza', 'origine']
    list_filter = ['dipendente', 'origine', 'assenza']
    date_hierarchy = 'data'
