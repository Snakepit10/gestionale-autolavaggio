from django.contrib import admin

from .models import EventoImpianto


@admin.register(EventoImpianto)
class EventoImpiantoAdmin(admin.ModelAdmin):
    """Consultazione eventi impianto (sola lettura: li scrive il listener)."""
    list_display = ('timestamp', 'nodo', 'tipo_evento', 'valore')
    list_filter = ('nodo', 'tipo_evento')
    date_hierarchy = 'timestamp'
    readonly_fields = ('nodo', 'tipo_evento', 'valore', 'payload', 'timestamp')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
