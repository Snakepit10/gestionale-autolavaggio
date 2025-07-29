from django.urls import path
from . import views

app_name = 'ordini'

urlpatterns = [
    # Punto Cassa
    path('cassa/', views.CassaView.as_view(), name='cassa'),
    path('cassa/mobile/', views.CassaMobileView.as_view(), name='cassa-mobile'),
    
    # AJAX Cassa
    path('api/aggiungi-carrello/', views.aggiungi_al_carrello, name='aggiungi-carrello'),
    path('api/rimuovi-carrello/', views.rimuovi_dal_carrello, name='rimuovi-carrello'),
    path('api/stato-carrello/', views.stato_carrello, name='stato-carrello'),
    path('api/applica-sconto/', views.applica_sconto, name='applica-sconto'),
    path('api/calcola-tempo-attesa/', views.calcola_tempo_attesa, name='calcola-tempo-attesa'),
    path('api/completa-ordine/', views.completa_ordine, name='completa-ordine'),
    
    # Gestione Ordini
    path('', views.OrdiniListView.as_view(), name='ordini-list'),
    path('<int:pk>/', views.OrdineDetailView.as_view(), name='ordine-detail'),
    path('<int:pk>/dettaglio/', views.dettaglio_ordine_json, name='dettaglio-json'),
    path('<int:pk>/registra-pagamento/', views.registra_pagamento, name='registra-pagamento'),
    path('<int:pk>/cambia-stato/', views.cambia_stato_ordine, name='cambia-stato'),
    path('<int:pk>/cambia-stato-pagamento/', views.cambia_stato_pagamento, name='cambia-stato-pagamento'),
    path('<int:pk>/modifica/', views.modifica_ordine, name='modifica-ordine'),
    path('<int:pk>/modifica-item/', views.modifica_item_ordine, name='modifica-item-ordine'),
    
    # Ordini speciali
    path('non-pagati/', views.OrdiniNonPagatiView.as_view(), name='ordini-non-pagati'),
    
    # Stampa
    path('<int:pk>/stampa/', views.stampa_ordine, name='stampa-ordine'),
    path('stampa/scontrino/<str:numero>/', views.stampa_scontrino, name='stampa-scontrino'),
]