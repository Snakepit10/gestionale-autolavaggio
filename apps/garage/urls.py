from django.urls import path

from . import views

app_name = 'garage'

urlpatterns = [
    path('registra/', views.registra, name='registra'),
    path('api/cerca-targa/', views.api_cerca_targa, name='cerca-targa'),
]
