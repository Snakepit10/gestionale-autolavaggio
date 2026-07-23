from django.urls import path

from . import views_client

app_name = 'monete_client'

urlpatterns = [
    path('', views_client.monete_home, name='home'),
    path('lavaggio/', views_client.lavaggio_scegli, name='lavaggio'),
    path('lavaggio/avvia/', views_client.lavaggio_avvia, name='lavaggio-avvia'),
]
