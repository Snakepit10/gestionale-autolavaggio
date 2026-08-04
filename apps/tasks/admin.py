from django.contrib import admin

from .models import CommentoTask, Etichetta, Progetto, Task


@admin.register(Progetto)
class ProgettoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'colore', 'ordine', 'archiviato', 'creato_da')
    list_filter = ('archiviato',)


@admin.register(Etichetta)
class EtichettaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'colore')


class CommentoTaskInline(admin.TabularInline):
    model = CommentoTask
    extra = 0
    readonly_fields = ('autore', 'creato_il')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('titolo', 'progetto', 'priorita', 'scadenza',
                    'visibilita', 'completata', 'creato_da')
    list_filter = ('completata', 'priorita', 'visibilita', 'progetto')
    search_fields = ('titolo', 'descrizione')
    autocomplete_fields = []
    filter_horizontal = ('assegnatari', 'etichette')
    inlines = [CommentoTaskInline]


@admin.register(CommentoTask)
class CommentoTaskAdmin(admin.ModelAdmin):
    list_display = ('task', 'autore', 'creato_il')
