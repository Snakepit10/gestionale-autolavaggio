from django.urls import path
from . import views

app_name = 'marketing'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('segmento/<str:chiave>/', views.segmento_dettaglio, name='segmento'),
    path('segmento/<str:chiave>/export/', views.segmento_export_csv, name='segmento-export'),
    path('impostazioni/', views.impostazioni, name='impostazioni'),
    path('cliente/<int:cliente_id>/toggle-optout/', views.toggle_opt_out, name='toggle-optout'),
]
