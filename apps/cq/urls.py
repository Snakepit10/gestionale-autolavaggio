from django.urls import path
from apps.cq import views

app_name = 'cq'

urlpatterns = [
    # Scheda CQ per un ordine
    path('ordine/<int:ordine_pk>/scheda/crea/', views.SchedaCQCreateView.as_view(), name='scheda_crea'),
    path('ordine/<int:ordine_pk>/scheda/', views.SchedaCQDetailView.as_view(), name='scheda_detail'),
    path('ordine/<int:ordine_pk>/scheda/modifica/', views.SchedaCQUpdateView.as_view(), name='scheda_modifica'),

    # Dashboard analitica
    path('dashboard/', views.DashboardCQView.as_view(), name='dashboard'),

    # Report mensile (titolari)
    path('report/<int:anno>/<int:mese>/', views.ReportMensileView.as_view(), name='report_mensile'),
    path('report/<int:anno>/<int:mese>/modifica-punteggio/', views.salva_modifica_punteggio, name='salva_modifica_punteggio'),
    path('report/<int:anno>/<int:mese>/valida/', views.valida_mese, name='valida_mese'),
    path('report/impostazione-premio/', views.salva_impostazione_premio, name='salva_impostazione_premio'),

    # Punteggio personale
    path('mio-punteggio/', views.MioPunteggioView.as_view(), name='mio_punteggio'),

    # Configurazione (solo titolare)
    path('configurazione/', views.ConfigurazioneCQView.as_view(), name='configurazione'),

    # API JSON per CRUD configurazione
    path('api/categoria-zona/salva/', views.api_salva_categoria_zona, name='api_cat_zona_salva'),
    path('api/categoria-zona/<int:pk>/elimina/', views.api_elimina_categoria_zona, name='api_cat_zona_elimina'),
    path('api/zona/salva/', views.api_salva_zona, name='api_zona_salva'),
    path('api/zona/<int:pk>/elimina/', views.api_elimina_zona, name='api_zona_elimina'),
    path('api/categoria-difetto/salva/', views.api_salva_categoria_difetto, name='api_cat_difetto_salva'),
    path('api/categoria-difetto/<int:pk>/elimina/', views.api_elimina_categoria_difetto, name='api_cat_difetto_elimina'),
    path('api/tipo-difetto/salva/', views.api_salva_tipo_difetto, name='api_tipo_difetto_salva'),
    path('api/tipo-difetto/<int:pk>/elimina/', views.api_elimina_tipo_difetto, name='api_tipo_difetto_elimina'),
    path('api/mapping/toggle/', views.api_toggle_mapping, name='api_mapping_toggle'),
]
