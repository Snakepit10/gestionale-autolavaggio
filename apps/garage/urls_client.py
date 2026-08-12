from django.urls import path

from . import views_client

app_name = 'garage_client'

urlpatterns = [
    path('', views_client.garage, name='garage'),
    path('aggiungi/', views_client.veicolo_aggiungi, name='veicolo-aggiungi'),
    path('<int:pk>/', views_client.scheda, name='scheda'),
    path('<int:pk>/percorso/', views_client.percorso, name='percorso'),
    path('<int:pk>/libretto/', views_client.libretto, name='libretto'),
    path('<int:pk>/elimina/', views_client.veicolo_elimina, name='veicolo-elimina'),
]
