from django.urls import path
from . import views
from apps.magazzino.views import vecchie_scorte

app_name = 'core'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    
    # CRUD Categorie
    path('categorie/', views.CategoriaListView.as_view(), name='categoria-list'),
    path('categorie/nuova/', views.CategoriaCreateView.as_view(), name='categoria-create'),
    path('categorie/<int:pk>/modifica/', views.CategoriaUpdateView.as_view(), name='categoria-update'),
    path('categorie/<int:pk>/elimina/', views.CategoriaDeleteView.as_view(), name='categoria-delete'),
    path('categorie/<int:pk>/sottocategoria/', views.rinomina_sottocategoria, name='categoria-sottocategoria'),
    
    # CRUD Servizi/Prodotti
    path('catalogo/', views.CatalogoListView.as_view(), name='catalogo-list'),
    path('catalogo/nuovo/', views.CatalogoCreateView.as_view(), name='catalogo-create'),
    path('catalogo/<int:pk>/modifica/', views.CatalogoUpdateView.as_view(), name='catalogo-update'),
    path('catalogo/<int:pk>/duplica/', views.duplica_servizio, name='catalogo-duplica'),
    path('catalogo/<int:pk>/elimina/', views.CatalogoDeleteView.as_view(), name='catalogo-delete'),
    path('catalogo/<int:pk>/toggle-pubblico/', views.toggle_mostra_pubblico, name='catalogo-toggle-pubblico'),
    
    # CRUD Sconti
    path('sconti/', views.ScontiListView.as_view(), name='sconti-list'),
    path('sconti/nuovo/', views.ScontoCreateView.as_view(), name='sconto-create'),
    path('sconti/<int:pk>/modifica/', views.ScontoUpdateView.as_view(), name='sconto-update'),
    path('sconti/<int:pk>/elimina/', views.ScontoDeleteView.as_view(), name='sconto-delete'),
    
    # Configurazione Stampanti
    path('stampanti/', views.StampantiListView.as_view(), name='stampanti-list'),
    path('stampanti/nuova/', views.StampanteCreateView.as_view(), name='stampante-create'),
    path('stampanti/<int:pk>/modifica/', views.StampanteUpdateView.as_view(), name='stampante-update'),
    path('stampanti/<int:pk>/test/', views.test_stampante, name='test-stampante'),
    
    # Gestione Scorte
    # Gestione Scorte -> app Magazzino (vecchi indirizzi rimandati la')
    path('scorte/', vecchie_scorte, name='scorte-list'),
    path('scorte/movimenti/', vecchie_scorte, name='movimenti-scorte'),
    path('scorte/movimento/', vecchie_scorte, name='movimento-scorte'),
    path('scorte/alert/', vecchie_scorte, name='alert-scorte'),
    
    # API
    path('api/servizi/', views.servizi_json, name='servizi-json'),
]