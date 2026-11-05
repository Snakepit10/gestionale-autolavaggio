from django.urls import path

from . import views

app_name = 'personale'

urlpatterns = [
    path('', views.mese, name='mese'),
    path('dipendenti/', views.dipendenti, name='dipendenti'),
    path('anno/', views.anno, name='anno'),
    path('previsione/', views.previsione, name='previsione'),
    path('previsione/dati.csv', views.previsione_dati, name='previsione_dati'),
]
