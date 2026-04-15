from django.contrib import admin
from .models import (
    SessioneTurno, PostazioneTurno, ChecklistItem,
    ChecklistCompilata, LavorazioneOperatore,
)


@admin.register(SessioneTurno)
class SessioneTurnoAdmin(admin.ModelAdmin):
    list_display = ['operatore', 'data_inizio', 'data_fine', 'stato']
    list_filter = ['stato', 'data_inizio']
    raw_id_fields = ['operatore']


@admin.register(PostazioneTurno)
class PostazioneTurnoAdmin(admin.ModelAdmin):
    list_display = ['sessione', 'postazione_cq', 'blocco']
    list_filter = ['postazione_cq']


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ['postazione_cq', 'blocco', 'nome', 'ordine', 'attivo']
    list_filter = ['postazione_cq', 'attivo']


@admin.register(ChecklistCompilata)
class ChecklistCompilataAdmin(admin.ModelAdmin):
    list_display = ['sessione', 'checklist_item', 'fase', 'esito', 'compilato_il']
    list_filter = ['fase', 'esito']


@admin.register(LavorazioneOperatore)
class LavorazioneOperatoreAdmin(admin.ModelAdmin):
    list_display = ['sessione', 'ordine', 'postazione_cq', 'stato', 'inizio', 'fine']
    list_filter = ['stato', 'postazione_cq']
    raw_id_fields = ['sessione', 'ordine']
