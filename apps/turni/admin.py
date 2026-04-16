from django.contrib import admin
from .models import (
    SessioneTurno, PostazioneTurno, ChecklistItem,
    ChecklistCompilata, LavorazioneOperatore,
    CategoriaChecklist, EsitoChecklist, VerificaChecklist,
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


@admin.register(CategoriaChecklist)
class CategoriaChecklistAdmin(admin.ModelAdmin):
    list_display = ['nome', 'icona', 'ordine']


@admin.register(EsitoChecklist)
class EsitoChecklistAdmin(admin.ModelAdmin):
    list_display = ['categoria', 'codice', 'nome', 'colore', 'ordine']
    list_filter = ['categoria']


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ['postazione_cq', 'blocco', 'categoria', 'nome', 'ordine', 'attivo']
    list_filter = ['postazione_cq', 'categoria', 'attivo']


@admin.register(ChecklistCompilata)
class ChecklistCompilataAdmin(admin.ModelAdmin):
    list_display = ['sessione', 'checklist_item', 'fase', 'esito', 'esito_obj', 'compilato_il']
    list_filter = ['fase', 'esito']


@admin.register(VerificaChecklist)
class VerificaChecklistAdmin(admin.ModelAdmin):
    list_display = ['compilata', 'verificato_da', 'esito_verifica', 'data_verifica']
    list_filter = ['esito_verifica']
    raw_id_fields = ['compilata']


@admin.register(LavorazioneOperatore)
class LavorazioneOperatoreAdmin(admin.ModelAdmin):
    list_display = ['sessione', 'ordine', 'postazione_cq', 'stato', 'inizio', 'fine']
    list_filter = ['stato', 'postazione_cq']
    raw_id_fields = ['sessione', 'ordine']
