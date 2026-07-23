from django.urls import path

from . import views_client

app_name = 'monete_client'

urlpatterns = [
    path('', views_client.monete_home, name='home'),
    path('lavaggio/', views_client.lavaggio_scegli, name='lavaggio'),
    path('lavaggio/avvia/', views_client.lavaggio_avvia, name='lavaggio-avvia'),
    path('acquista/<int:pacchetto_id>/', views_client.acquista, name='acquista'),
    path('acquisto/esito/', views_client.acquisto_esito, name='acquisto-esito'),
    path('acquisto/annullato/', views_client.acquisto_annullato, name='acquisto-annullato'),
    path('paypal/ritorno/', views_client.paypal_ritorno, name='paypal-ritorno'),
]
