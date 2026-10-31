from django.urls import path

from . import views, views_analitica, views_bilancio, views_prima_nota

app_name = 'contabilita'

urlpatterns = [
    path('', views.fatture, name='fatture'),
    path('fatture/<int:pk>/', views.fattura, name='fattura'),
    path('scadenziario/', views.scadenziario, name='scadenziario'),
    path('piano-dei-conti/', views.piano_conti, name='piano_conti'),
    path('prima-nota/', views_prima_nota.prima_nota, name='prima_nota'),
    path('prima-nota/<int:pk>/', views_prima_nota.movimento, name='movimento'),
    path('conti/', views_prima_nota.conti, name='conti'),
    path('regole/', views_prima_nota.regole, name='regole'),
    path('conto-economico/', views_bilancio.conto_economico, name='conto_economico'),
    path('conto-economico/dettaglio/', views_bilancio.dettaglio, name='dettaglio'),
    path('flussi-di-cassa/', views_bilancio.flussi_di_cassa, name='flussi'),
    path('cespiti/', views_bilancio.cespiti, name='cespiti'),
    path('analitica/', views_analitica.analitica, name='analitica'),
    path('analitica/impostazioni/', views_analitica.impostazioni_analitica, name='impostazioni_analitica'),
]
