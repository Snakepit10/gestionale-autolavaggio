from django.urls import path
from . import views
# Temporaneamente commentato - da riabilitare quando le app saranno abilitate
# from apps.prenotazioni.views import (
#     AreaClienteDashboard, ProfiloClienteView, AbbonamentiClienteView,
#     PuntiFedeltaClienteView, StatisticheClienteView, OrdiniClienteView
# )

app_name = 'clienti'

urlpatterns = [
    # CRUD Clienti (Admin)
    path('admin/', views.ClientiListView.as_view(), name='clienti-list'),
    path('admin/nuovo/', views.ClienteCreateView.as_view(), name='cliente-create'),
    path('admin/<int:pk>/modifica/', views.ClienteUpdateView.as_view(), name='cliente-update'),
    path('admin/<int:pk>/elimina/', views.ClienteDeleteView.as_view(), name='cliente-delete'),
    path('admin/<int:pk>/storico/', views.StoricoClienteView.as_view(), name='storico-cliente'),
    
    # AJAX e utility
    path('cerca/', views.cerca_cliente, name='cerca-cliente'),
    path('crea-ajax/', views.crea_cliente_ajax, name='crea-cliente-ajax'),
    path('admin/<int:pk>/invia-credenziali/', views.invia_credenziali_cliente, name='invia-credenziali'),
    path('admin/<int:pk>/gestisci-punti/', views.gestisci_punti_fedelta, name='gestisci-punti'),
    
    # Export
    path('export/csv/', views.export_clienti_csv, name='export-clienti-csv'),
    
    # Area Cliente - temporaneamente commentata
    # path('area-cliente/', AreaClienteDashboard.as_view(), name='area-cliente'),
    # path('area-cliente/profilo/', ProfiloClienteView.as_view(), name='profilo-cliente'),
    # path('area-cliente/abbonamenti/', AbbonamentiClienteView.as_view(), name='abbonamenti-cliente'),
    # path('area-cliente/punti/', PuntiFedeltaClienteView.as_view(), name='punti-cliente'),
    # path('area-cliente/statistiche/', StatisticheClienteView.as_view(), name='statistiche-cliente'),
    # path('area-cliente/ordini/', OrdiniClienteView.as_view(), name='ordini-cliente'),
]