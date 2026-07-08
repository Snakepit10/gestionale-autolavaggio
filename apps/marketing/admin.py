from django.contrib import admin

from .models import Campagna, ImpostazioniMarketing, InvioCampagna


@admin.register(ImpostazioniMarketing)
class ImpostazioniMarketingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Singleton: si crea solo via get_solo(), mai due righe.
        return not ImpostazioniMarketing.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class InvioCampagnaInline(admin.TabularInline):
    model = InvioCampagna
    extra = 0
    readonly_fields = ['cliente', 'stato', 'inviato_il', 'motivo_salto', 'messaggio_wa']
    can_delete = False


@admin.register(Campagna)
class CampagnaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'tipo', 'stato', 'creata_il', 'lanciata_il', 'n_destinatari', 'n_inviati', 'n_falliti']
    list_filter = ['tipo', 'stato']
    search_fields = ['nome']
    readonly_fields = ['creata_il', 'lanciata_il', 'completata_il']
    inlines = [InvioCampagnaInline]


@admin.register(InvioCampagna)
class InvioCampagnaAdmin(admin.ModelAdmin):
    list_display = ['campagna', 'cliente', 'stato', 'inviato_il', 'motivo_salto']
    list_filter = ['stato', 'campagna']
    search_fields = ['cliente__nome', 'cliente__cognome', 'cliente__telefono']
    autocomplete_fields = ['cliente']
