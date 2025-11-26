from django.urls import path
from . import views

app_name = 'finanze'

urlpatterns = [
    # Dashboard chiusura cassa
    path('', views.chiusura_cassa_dashboard, name='dashboard'),

    # Operazioni cassa
    path('apri/', views.apri_cassa, name='apri_cassa'),
    path('chiudi/', views.chiudi_cassa, name='chiudi_cassa'),
    path('conferma/<int:chiusura_id>/', views.conferma_chiusura, name='conferma_chiusura'),

    # Movimenti
    path('movimento/aggiungi/', views.aggiungi_movimento, name='aggiungi_movimento'),

    # Storico
    path('storico/', views.storico_chiusure, name='storico_chiusure'),
    path('dettaglio/<int:chiusura_id>/', views.dettaglio_chiusura, name='dettaglio_chiusura'),
]
