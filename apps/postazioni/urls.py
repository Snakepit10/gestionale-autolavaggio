from django.urls import path
from . import views

app_name = 'postazioni'

urlpatterns = [
    # CRUD Postazioni
    path('', views.PostazioniListView.as_view(), name='postazioni-list'),
    path('nuova/', views.PostazioneCreateView.as_view(), name='postazione-create'),
    path('<int:pk>/modifica/', views.PostazioneUpdateView.as_view(), name='postazione-update'),
    path('<int:pk>/elimina/', views.PostazioneDeleteView.as_view(), name='postazione-delete'),
    
    # Dashboard Postazione
    path('<int:pk>/dashboard/', views.DashboardPostazione.as_view(), name='dashboard-postazione'),
    
    # AJAX Actions
    path('<int:postazione_id>/item/<int:item_id>/aggiorna-stato/', 
         views.aggiorna_stato_item, name='aggiorna-stato-item'),
    
    # Utilità
    path('ora-server/', views.ora_server, name='ora-server'),
    
    # Stampa
    path('<int:postazione_id>/stampa-comanda/<str:ordine_numero>/', 
         views.stampa_comanda_postazione, name='stampa-comanda-postazione'),
    
    # Dashboard TV
    path('dashboard-tv/', views.DashboardTVView.as_view(), name='dashboard-tv'),
    path('dashboard-tv/data/', views.dashboard_tv_data, name='dashboard-tv-data'),
]