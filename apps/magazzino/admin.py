from django.contrib import admin

from .models import (Articolo, Assegnazione, Consegna, Fornitore, Movimento, OrdineFornitore,
                     RigaConsegna, RigaOrdineFornitore, StatoAssegnazione)


@admin.register(Fornitore)
class FornitoreAdmin(admin.ModelAdmin):
    list_display = ('ragione_sociale', 'referente', 'telefono', 'email', 'attivo')
    search_fields = ('ragione_sociale',)


@admin.register(Articolo)
class ArticoloAdmin(admin.ModelAdmin):
    list_display = ('nome', 'tipo', 'categoria', 'quantita', 'scorta_minima', 'traccia_scorte', 'attivo')
    list_filter = ('tipo', 'attivo', 'traccia_scorte')
    search_fields = ('nome', 'codice')
    # la quantita' cambia solo con i movimenti
    readonly_fields = ('quantita',)


@admin.register(Movimento)
class MovimentoAdmin(admin.ModelAdmin):
    list_display = ('data', 'articolo', 'tipo', 'quantita', 'quantita_dopo', 'operatore')
    list_filter = ('tipo',)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


class RigaOrdineInline(admin.TabularInline):
    model = RigaOrdineFornitore
    extra = 0


@admin.register(OrdineFornitore)
class OrdineFornitoreAdmin(admin.ModelAdmin):
    list_display = ('numero', 'fornitore', 'data', 'stato')
    list_filter = ('stato',)
    inlines = [RigaOrdineInline]


class RigaConsegnaInline(admin.TabularInline):
    model = RigaConsegna
    extra = 0


@admin.register(Consegna)
class ConsegnaAdmin(admin.ModelAdmin):
    list_display = ('data', 'fornitore', 'numero_ddt', 'ordine')
    inlines = [RigaConsegnaInline]


class StatoInline(admin.TabularInline):
    model = StatoAssegnazione
    extra = 0


@admin.register(Assegnazione)
class AssegnazioneAdmin(admin.ModelAdmin):
    list_display = ('articolo', 'quantita', 'utente', 'postazione', 'stato', 'data', 'da_gestire')
    list_filter = ('stato', 'da_gestire')
    inlines = [StatoInline]
