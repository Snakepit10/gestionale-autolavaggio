from django.contrib import admin

from .models import (AcquistoMonete, ImpostazioniMonete, MovimentoMoneta,
                     NodoImpianto, PacchettoMonete, SaldoMonete)


@admin.register(NodoImpianto)
class NodoImpiantoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'slug', 'switch_id', 'monete_per_impulso',
                    'max_impulsi', 'attivo', 'ordine')
    list_filter = ('attivo',)
    prepopulated_fields = {'slug': ('nome',)}


@admin.register(SaldoMonete)
class SaldoMoneteAdmin(admin.ModelAdmin):
    """Sola consultazione: il saldo si muove SOLO via services/wallet
    (accrediti/addebiti atomici con movimento di ledger)."""
    list_display = ('cliente', 'saldo', 'aggiornato_il')
    search_fields = ('cliente__nome', 'cliente__cognome', 'cliente__telefono')
    readonly_fields = ('cliente', 'saldo', 'aggiornato_il')

    def has_add_permission(self, request):
        return False


@admin.register(MovimentoMoneta)
class MovimentoMonetaAdmin(admin.ModelAdmin):
    list_display = ('creato_il', 'cliente', 'tipo', 'monete', 'saldo_dopo',
                    'nodo', 'operatore', 'descrizione')
    list_filter = ('tipo', 'nodo')
    search_fields = ('cliente__nome', 'cliente__cognome', 'descrizione')
    date_hierarchy = 'creato_il'
    readonly_fields = [f.name for f in MovimentoMoneta._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PacchettoMonete)
class PacchettoMoneteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'monete', 'bonus', 'prezzo', 'attivo', 'ordine')
    list_filter = ('attivo',)


@admin.register(AcquistoMonete)
class AcquistoMoneteAdmin(admin.ModelAdmin):
    list_display = ('creato_il', 'cliente', 'monete', 'importo', 'provider',
                    'stato', 'provider_ref')
    list_filter = ('provider', 'stato')
    search_fields = ('cliente__nome', 'cliente__cognome', 'provider_ref')
    readonly_fields = [f.name for f in AcquistoMonete._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ImpostazioniMonete)
class ImpostazioniMoneteAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ImpostazioniMonete.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
