from django.urls import path
from . import views

app_name = 'clients'

urlpatterns = [
    # Pubblico
    path('', views.landing, name='landing'),
    path('registrati/', views.register, name='register'),
    path('servizi/', views.booking, name='booking'),

    # Cliente loggato
    path('area/', views.dashboard, name='dashboard'),
    path('annulla/<int:pk>/', views.annulla_prenotazione, name='annulla'),

    # API JSON
    path('api/slot/', views.slot_disponibili_pub, name='api_slot'),
    path('api/prenota/', views.crea_prenotazione_pub, name='api_prenota'),

    # Diagnostica (solo staff)
    path('api/test-email/', views.test_email, name='test_email'),
]
