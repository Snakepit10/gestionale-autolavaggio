from django.urls import path
from . import views

app_name = 'prenotazioni'

urlpatterns = [
    # Configurazione Slot
    path('configurazione/slot/', views.ConfigurazioneSlotListView.as_view(), name='config-slot-list'),
    path('configurazione/slot/nuovo/', views.ConfigurazioneSlotCreateView.as_view(), name='config-slot-create'),
    path('configurazione/slot/<int:pk>/modifica/', views.ConfigurazioneSlotUpdateView.as_view(), name='config-slot-update'),
    path('configurazione/slot/<int:pk>/duplica/', views.duplica_configurazione_slot, name='duplica-config-slot'),
    path('configurazione/slot/<int:pk>/elimina/', views.elimina_configurazione_slot, name='elimina-config-slot'),
    
    # Prenotazioni Cliente
    path('', views.PrenotazioniView.as_view(), name='prenotazioni'),
    path('calendario/', views.CalendarioPrenotazioniView.as_view(), name='calendario-prenotazioni'),
    path('nuova/', views.NuovaPrenotazioneView.as_view(), name='nuova-prenotazione'),
    path('nuova/classica/', views.NuovaPrenotazioneClassicaView.as_view(), name='nuova-prenotazione-classica'),
    path('<int:pk>/', views.DettaglioPrenotazioneView.as_view(), name='dettaglio-prenotazione'),
    path('<int:pk>/modifica/', views.ModificaPrenotazioneView.as_view(), name='modifica-prenotazione'),
    path('<int:pk>/elimina/', views.EliminaPrenotazioneView.as_view(), name='elimina-prenotazione'),
    path('<int:pk>/annulla/', views.annulla_prenotazione, name='annulla-prenotazione'),
    
    # API
    path('api/prenotazione-rapida/', views.prenotazione_rapida_api, name='prenotazione-rapida'),
    path('api/cerca-clienti/', views.cerca_clienti_api, name='cerca-clienti'),
    path('api/slot-disponibili/', views.slot_disponibili_api, name='slot-disponibili'),
    path('api/calendario-mese/', views.calendario_mese_api, name='calendario-mese'),
    path('api/calendario-settimana/', views.calendario_settimana_api, name='calendario-settimana'),
    path('api/statistiche-calendario/', views.statistiche_calendario_api, name='statistiche-calendario'),
    
    # Admin Prenotazioni
    path('admin/', views.PrenotazioniAdminListView.as_view(), name='prenotazioni-admin'),
    path('checkin/', views.CheckinPrenotazioniView.as_view(), name='checkin-prenotazioni'),
    path('admin/<int:pk>/checkin/', views.checkin_prenotazione, name='checkin-prenotazione'),
    path('admin/<int:pk>/cancella/', views.cancella_prenotazione, name='cancella-prenotazione'),
]