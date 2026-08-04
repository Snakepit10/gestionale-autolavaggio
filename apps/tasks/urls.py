from django.urls import path

from . import views

app_name = 'tasks'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nuova/', views.task_crea, name='task-crea'),
    path('<int:pk>/', views.task_dettaglio, name='task-dettaglio'),
    path('<int:pk>/modifica/', views.task_modifica, name='task-modifica'),
    path('<int:pk>/toggle/', views.task_toggle, name='task-toggle'),
    path('<int:pk>/elimina/', views.task_elimina, name='task-elimina'),
    path('<int:pk>/commenti/nuovo/', views.commento_crea, name='commento-crea'),
    path('<int:pk>/sottotask/nuova/', views.sottotask_crea, name='sottotask-crea'),

    path('progetti/', views.progetti, name='progetti'),
    path('progetti/<int:pk>/modifica/', views.progetto_modifica, name='progetto-modifica'),
    path('progetti/<int:pk>/archivia/', views.progetto_archivia, name='progetto-archivia'),
    path('etichette/', views.etichette, name='etichette'),
    path('etichette/<int:pk>/elimina/', views.etichetta_elimina, name='etichetta-elimina'),

    path('api/conteggio/', views.api_conteggio, name='api-conteggio'),
]
