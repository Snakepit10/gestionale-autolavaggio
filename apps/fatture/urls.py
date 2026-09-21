from django.urls import path

from . import views

app_name = 'fatture'

urlpatterns = [
    path('', views.fatture_home, name='fatture-list'),
    path('api/suggerisci-numero/', views.suggerisci_numero,
         name='suggerisci-numero'),
    path('api/crea/', views.crea_fattura, name='crea-fattura'),
    path('api/voce-manuale/', views.crea_voce_manuale,
         name='crea-voce-manuale'),
    path('api/voce/<int:pk>/elimina/', views.elimina_voce,
         name='elimina-voce'),
    path('api/<int:pk>/modifica/', views.modifica_fattura, name='modifica'),
    path('api/<int:pk>/segna-pagata/', views.segna_pagata,
         name='segna-pagata'),
    path('api/<int:pk>/archivia/', views.archivia_fattura, name='archivia'),
    path('api/<int:pk>/elimina/', views.elimina_fattura, name='elimina'),
]
