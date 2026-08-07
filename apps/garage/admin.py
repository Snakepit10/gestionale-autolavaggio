from django.contrib import admin

from .models import (BadgeVeicolo, EventoLibretto, ImpostazioniGarage,
                     ItemLivello, LivelloPercorso, NotificaGarage, Percorso,
                     PremioVeicolo, TipoServizioGarage, Veicolo)


class ItemLivelloInline(admin.TabularInline):
    model = ItemLivello
    extra = 0


class LivelloPercorsoInline(admin.TabularInline):
    model = LivelloPercorso
    extra = 0
    show_change_link = True


@admin.register(Percorso)
class PercorsoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'predefinito', 'attivo')
    inlines = [LivelloPercorsoInline]


@admin.register(LivelloPercorso)
class LivelloPercorsoAdmin(admin.ModelAdmin):
    list_display = ('percorso', 'numero', 'nome', 'premio_gettoni', 'badge_nome')
    list_filter = ('percorso',)
    inlines = [ItemLivelloInline]


@admin.register(TipoServizioGarage)
class TipoServizioGarageAdmin(admin.ModelAdmin):
    list_display = ('nome', 'slug', 'punti_salute', 'validita_giorni',
                    'badge_nome', 'attivo', 'ordine')
    list_filter = ('attivo',)
    prepopulated_fields = {'slug': ('nome',)}


@admin.register(ImpostazioniGarage)
class ImpostazioniGarageAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ImpostazioniGarage.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Veicolo)
class VeicoloAdmin(admin.ModelAdmin):
    list_display = ('targa', 'cliente', 'marca', 'modello',
                    'punteggio_salute', 'streak_conteggio', 'attivo')
    search_fields = ('targa', 'cliente__nome', 'cliente__cognome',
                     'cliente__telefono')
    list_filter = ('attivo',)
    autocomplete_fields = []


@admin.register(EventoLibretto)
class EventoLibrettoAdmin(admin.ModelAdmin):
    list_display = ('veicolo', 'tipo_servizio', 'origine', 'data',
                    'registrato_da')
    list_filter = ('origine', 'tipo_servizio')
    search_fields = ('veicolo__targa',)
    date_hierarchy = 'data'


@admin.register(BadgeVeicolo)
class BadgeVeicoloAdmin(admin.ModelAdmin):
    list_display = ('veicolo', 'nome', 'slug', 'ottenuto_il')
    search_fields = ('veicolo__targa',)


@admin.register(PremioVeicolo)
class PremioVeicoloAdmin(admin.ModelAdmin):
    list_display = ('veicolo', 'chiave', 'gettoni', 'creato_il')
    search_fields = ('veicolo__targa', 'chiave')


@admin.register(NotificaGarage)
class NotificaGarageAdmin(admin.ModelAdmin):
    list_display = ('veicolo', 'tipo', 'inviata', 'creata_il')
    list_filter = ('tipo', 'inviata')
