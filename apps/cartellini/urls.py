from django.urls import path

from . import views

app_name = 'cartellini'

urlpatterns = [
    path('', views.generatore, name='generatore'),
    path('api/sets/', views.sets_list, name='sets-list'),
    path('api/sets/<int:pk>/', views.sets_detail, name='sets-detail'),
    path('api/sets/<int:pk>/duplica/', views.sets_duplica, name='sets-duplica'),
]
