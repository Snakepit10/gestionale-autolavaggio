from django.urls import path

from . import views

app_name = 'magazzino'

urlpatterns = [
    path('', views.articoli, name='articoli'),
    path('articoli/salva/', views.articolo_salva, name='articolo-salva'),
    path('articoli/<int:pk>/', views.articolo_scheda, name='articolo'),
    path('articoli/<int:pk>/movimento/', views.articolo_movimento, name='articolo-movimento'),
    path('articoli/<int:pk>/foto/carica/', views.articolo_foto_carica, name='articolo-foto-carica'),
    path('foto/<int:pk>/', views.articolo_foto, name='articolo-foto'),
    path('movimenti/', views.movimenti, name='movimenti'),

    path('posti/', views.posti, name='posti'),
    path('posti/salva/', views.posto_salva, name='posto-salva'),
    path('posti/<int:pk>/elimina/', views.posto_elimina, name='posto-elimina'),

    path('fornitori/', views.fornitori, name='fornitori'),
    path('fornitori/salva/', views.fornitore_salva, name='fornitore-salva'),

    path('ordini/', views.ordini, name='ordini'),
    path('ordini/nuovo/', views.ordine_scheda, name='ordine-nuovo'),
    path('ordini/salva/', views.ordine_salva, name='ordine-salva'),
    path('ordini/proponi/', views.proponi_sotto_scorta, name='ordine-proponi'),
    path('ordini/<int:pk>/', views.ordine_scheda, name='ordine'),
    path('ordini/<int:pk>/stato/', views.ordine_stato, name='ordine-stato'),
    path('ordini/<int:pk>/elimina/', views.ordine_elimina, name='ordine-elimina'),
    path('ordini/<int:pk>/stampa/', views.ordine_stampa, name='ordine-stampa'),
    path('ordini/<int:pk>/residuo/', views.ordine_residuo, name='ordine-residuo'),

    path('consegne/', views.consegne, name='consegne'),
    path('consegne/registra/', views.consegna_registra, name='consegna-registra'),
    path('consegne/<int:pk>/elimina/', views.consegna_elimina, name='consegna-elimina'),

    path('assegnazioni/', views.assegnazioni, name='assegnazioni'),
    path('assegnazioni/nuova/', views.assegnazione_nuova, name='assegnazione-nuova'),
    path('assegnazioni/<int:pk>/modifica/', views.assegnazione_modifica, name='assegnazione-modifica'),
    path('assegnazioni/<int:pk>/stato/', views.assegnazione_stato, name='assegnazione-stato'),
    path('assegnazioni/<int:pk>/sostituisci/', views.assegnazione_sostituisci, name='assegnazione-sostituisci'),
    path('assegnazioni/<int:pk>/gestita/', views.assegnazione_gestita, name='assegnazione-gestita'),
    path('segnalazioni/', views.segnalazioni, name='segnalazioni'),

    path('mia-dotazione/', views.mia_dotazione, name='mia-dotazione'),
    path('mia-dotazione/<int:pk>/stato/', views.mia_dotazione_stato, name='mia-dotazione-stato'),
    path('mia-dotazione/schede/', views.schede_prodotti, name='schede-prodotti'),

    path('report/', views.report, name='report'),
]
