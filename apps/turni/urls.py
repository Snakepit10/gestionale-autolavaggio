from django.urls import path
from apps.turni import views

app_name = 'turni'

urlpatterns = [
    # Flusso operatore
    path('selezione-postazioni/', views.selezione_postazioni, name='selezione_postazioni'),
    path('checklist/', views.checklist_view, name='checklist'),
    path('checklist-fine/', views.checklist_fine_view, name='checklist_fine'),
    path('dashboard/', views.dashboard_operatore, name='dashboard'),
    path('chiudi-turno/', views.chiudi_turno, name='chiudi_turno'),

    # API operatore (AJAX)
    path('api/ordine/<int:ordine_id>/dettaglio/', views.api_ordine_dettaglio, name='api_ordine_dettaglio'),
    path('api/ordine/<int:ordine_id>/inizia-lavoro/', views.api_inizia_lavoro, name='api_inizia_lavoro'),
    path('api/lavorazione/<int:lav_id>/pausa/', views.api_pausa_lavoro, name='api_pausa_lavoro'),
    path('api/lavorazione/<int:lav_id>/riprendi/', views.api_riprendi_lavoro, name='api_riprendi_lavoro'),
    path('api/lavorazione/<int:lav_id>/completa/', views.api_completa_lavoro, name='api_completa_lavoro'),
    path('api/ordine/<int:ordine_id>/aggiungi-item/', views.api_aggiungi_item, name='api_aggiungi_item'),
    path('api/ordine/<int:ordine_id>/segnala-difetto/', views.api_segnala_difetto, name='api_segnala_difetto'),
    path('api/coda/', views.api_coda_ordini, name='api_coda_ordini'),

    # Configurazione checklist (titolare)
    path('configurazione/checklist/', views.config_checklist, name='config_checklist'),
    path('api/checklist-item/salva/', views.api_salva_checklist_item, name='api_checklist_item_salva'),
    path('api/checklist-item/<int:pk>/elimina/', views.api_elimina_checklist_item, name='api_checklist_item_elimina'),
    path('api/categoria-checklist/salva/', views.api_salva_categoria, name='api_categoria_salva'),
    path('api/categoria-checklist/<int:pk>/elimina/', views.api_elimina_categoria, name='api_categoria_elimina'),
    path('api/esito-checklist/salva/', views.api_salva_esito, name='api_esito_salva'),
    path('api/esito-checklist/<int:pk>/elimina/', views.api_elimina_esito, name='api_esito_elimina'),

    # Storico checklist (responsabile/titolare)
    path('storico-checklist/', views.storico_checklist, name='storico_checklist'),
    path('api/verifica/<int:compilata_id>/', views.api_salva_verifica, name='api_salva_verifica'),

    # Report
    path('report/', views.report_lavorazioni, name='report_lavorazioni'),
    path('report/ordine/<int:ordine_id>/', views.report_ordine_dettaglio, name='report_ordine_dettaglio'),
    path('api/report/dati/', views.api_report_dati, name='api_report_dati'),
]
