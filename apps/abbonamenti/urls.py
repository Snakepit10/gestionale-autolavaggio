from django.urls import path
from . import views

app_name = 'abbonamenti'

urlpatterns = [
    # Configurazioni Abbonamento
    path('configurazioni/', views.ConfigurazioniAbbonamentoListView.as_view(), name='config-abbonamenti-list'),
    path('configurazioni/nuova/', views.WizardConfigurazioneAbbonamento.as_view(), name='config-abbonamento-wizard'),
    path('configurazioni/<int:pk>/modifica/', views.ConfigurazioneAbbonamentoUpdateView.as_view(), name='config-abbonamento-update'),
    path('configurazioni/<int:pk>/elimina/', views.ConfigurazioneAbbonamentoDeleteView.as_view(), name='config-abbonamento-delete'),
    path('configurazioni/<int:pk>/dettagli-json/', views.dettagli_configurazione_json, name='dettagli-configurazione-json'),
    path('configurazioni/<int:pk>/clona/', views.clona_configurazione, name='clona-configurazione'),
    
    # Gestione Abbonamenti
    path('', views.AbbonamentiListView.as_view(), name='abbonamenti-list'),
    path('nuovo/', views.VenditaAbbonamentoView.as_view(), name='vendita-abbonamento'),
    path('<int:pk>/', views.DettaglioAbbonamentoView.as_view(), name='dettaglio-abbonamento'),
    path('<int:pk>/rinnova/', views.rinnova_abbonamento, name='rinnova-abbonamento'),
    path('<int:pk>/sospendi/', views.sospendi_abbonamento, name='sospendi-abbonamento'),
    path('in-scadenza/', views.AbbonamentiInScadenzaView.as_view(), name='abbonamenti-scadenza'),
    
    # Verifica Accessi
    path('verifica/', views.VerificaAccessoView.as_view(), name='verifica-accesso'),
    path('verifica/<str:codice>/', views.VerificaAbbonamentoView.as_view(), name='verifica-abbonamento'),
    path('verifica/<str:codice>/registra-accesso/', views.registra_accesso_abbonamento, name='registra-accesso'),
]