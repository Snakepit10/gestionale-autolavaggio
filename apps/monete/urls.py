from django.urls import path

from . import views

app_name = 'monete'

urlpatterns = [
    path('avvia/', views.avvia_staff, name='avvia'),
    path('cliente/<int:pk>/movimento/', views.movimento_cliente, name='movimento'),
    path('webhook/stripe/', views.webhook_stripe, name='webhook-stripe'),
]
