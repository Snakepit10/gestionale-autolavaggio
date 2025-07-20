from django.contrib import admin
from .models import ConfigurazioneSlot, SlotPrenotazione, Prenotazione, CalendarioPersonalizzato


@admin.register(ConfigurazioneSlot)
class ConfigurazioneSlotAdmin(admin.ModelAdmin):
    list_display = ['get_giorno_display', 'ora_inizio', 'ora_fine', 'durata_slot_minuti', 'max_prenotazioni_per_slot', 'attivo']
    list_filter = ['giorno_settimana', 'attivo']
    filter_horizontal = ['servizi_ammessi']
    
    def get_giorno_display(self, obj):
        return obj.get_giorno_settimana_display()
    get_giorno_display.short_description = 'Giorno'


@admin.register(SlotPrenotazione)
class SlotPrenotazioneAdmin(admin.ModelAdmin):
    list_display = ['data', 'ora_inizio', 'ora_fine', 'posti_disponibili', 'prenotazioni_attuali']
    list_filter = ['data', 'disponibile']
    search_fields = ['data']
    readonly_fields = ['prenotazioni_attuali']


@admin.register(Prenotazione)
class PrenotazioneAdmin(admin.ModelAdmin):
    list_display = ['codice_prenotazione', 'cliente', 'slot', 'stato', 'creata_il']
    list_filter = ['stato', 'creata_il', 'slot__data']
    search_fields = ['codice_prenotazione', 'cliente__nome', 'cliente__cognome']
    readonly_fields = ['codice_prenotazione', 'creata_il']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cliente', 'slot')


@admin.register(CalendarioPersonalizzato)
class CalendarioPersonalizzatoAdmin(admin.ModelAdmin):
    list_display = ['data', 'chiuso', 'orario_speciale_inizio', 'orario_speciale_fine', 'note']
    list_filter = ['data', 'chiuso']
    search_fields = ['note']