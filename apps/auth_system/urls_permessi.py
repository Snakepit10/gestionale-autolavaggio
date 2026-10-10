from django.urls import path

from . import views_permessi as views

app_name = 'permessi'

urlpatterns = [
    path('', views.matrice, name='matrice'),
    path('api/gruppo/', views.api_gruppo, name='api-gruppo'),
    path('api/eccezione/', views.api_eccezione, name='api-eccezione'),
    path('api/eccezione/<int:pk>/elimina/', views.api_eccezione_elimina, name='api-eccezione-elimina'),
]
