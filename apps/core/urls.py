from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    
    # CRUD Categorie
    path('categorie/', views.CategoriaListView.as_view(), name='categoria-list'),
    path('categorie/nuova/', views.CategoriaCreateView.as_view(), name='categoria-create'),
    path('categorie/<int:pk>/modifica/', views.CategoriaUpdateView.as_view(), name='categoria-update'),
    path('categorie/<int:pk>/elimina/', views.CategoriaDeleteView.as_view(), name='categoria-delete'),
    
    # CRUD Servizi/Prodotti
    path('catalogo/', views.CatalogoListView.as_view(), name='catalogo-list'),
    path('catalogo/nuovo/', views.CatalogoCreateView.as_view(), name='catalogo-create'),
    path('catalogo/<int:pk>/modifica/', views.CatalogoUpdateView.as_view(), name='catalogo-update'),
    path('catalogo/<int:pk>/elimina/', views.CatalogoDeleteView.as_view(), name='catalogo-delete'),
    
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
    path('scorte/', views.ScorteListView.as_view(), name='scorte-list'),
    path('scorte/movimenti/', views.MovimentiScorteView.as_view(), name='movimenti-scorte'),
    path('scorte/movimento/', views.movimento_scorte, name='movimento-scorte'),
    path('scorte/alert/', views.ProdottiSottoScortaView.as_view(), name='alert-scorte'),
    
    # API
    path('api/servizi/', views.servizi_json, name='servizi-json'),
]