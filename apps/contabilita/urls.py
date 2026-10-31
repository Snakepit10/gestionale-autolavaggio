from django.urls import path

from . import views

app_name = 'contabilita'

urlpatterns = [
    path('', views.fatture, name='fatture'),
    path('fatture/<int:pk>/', views.fattura, name='fattura'),
    path('scadenziario/', views.scadenziario, name='scadenziario'),
    path('piano-dei-conti/', views.piano_conti, name='piano_conti'),
]
