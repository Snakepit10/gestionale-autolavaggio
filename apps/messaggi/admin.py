from django.contrib import admin

from .models import ConversazioneWhatsApp, MessaggioWhatsApp


@admin.register(ConversazioneWhatsApp)
class ConversazioneWhatsAppAdmin(admin.ModelAdmin):
    list_display = ('numero_e164', 'cliente', 'non_letti',
                    'ultimo_messaggio_il', 'ultimo_incoming_il')
    list_filter = ('non_letti',)
    search_fields = ('numero_e164', 'cliente__nome', 'cliente__cognome',
                     'cliente__telefono')
    readonly_fields = ('creata_il', 'ultimo_messaggio_il')
    autocomplete_fields = ('cliente',)


@admin.register(MessaggioWhatsApp)
class MessaggioWhatsAppAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversazione', 'direzione', 'stato',
                    'corpo_short', 'creato_il')
    list_filter = ('direzione', 'stato')
    search_fields = ('corpo', 'wa_message_id', 'conversazione__numero_e164')
    readonly_fields = ('wa_message_id', 'timestamp_meta', 'creato_il', 'aggiornato_il')
    raw_id_fields = ('conversazione', 'operatore')

    def corpo_short(self, obj):
        return (obj.corpo or '')[:80]
    corpo_short.short_description = 'Corpo'
