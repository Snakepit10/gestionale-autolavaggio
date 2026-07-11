from django.urls import path
from . import views

app_name = 'marketing'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('segmento/<str:chiave>/', views.segmento_dettaglio, name='segmento'),
    path('segmento/<str:chiave>/export/', views.segmento_export_csv, name='segmento-export'),
    path('impostazioni/', views.impostazioni, name='impostazioni'),
    path('cliente/<int:cliente_id>/toggle-optout/', views.toggle_opt_out, name='toggle-optout'),

    # Campagne
    path('campagne/', views.campagne_list, name='campagne'),
    path('campagne/nuova/', views.campagna_nuova, name='campagna-nuova'),
    path('campagne/preview/', views.campagna_preview, name='campagna-preview'),
    path('campagne/crea/', views.campagna_crea, name='campagna-crea'),
    path('campagne/<int:pk>/', views.campagna_dettaglio, name='campagna-dettaglio'),
    path('campagne/<int:pk>/annulla/', views.campagna_annulla, name='campagna-annulla'),
    path('campagne/processa-coda/', views.processa_coda_ora, name='processa-coda'),
]
