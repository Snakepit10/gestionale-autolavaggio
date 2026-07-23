from django.urls import path

from apps.impianto.views import test_moneta

from . import views

app_name = 'api'

urlpatterns = [
    # === WhatsApp webhook (chiamato da Meta Cloud API) ===
    # GET: verifica setup one-time con hub.challenge.
    # POST: ricezione messaggi clienti + status updates outgoing.
    path('whatsapp/webhook/', views.whatsapp_webhook, name='whatsapp-webhook'),

    # === Endpoint REST per la inbox /messaggi/ (chiamati dal frontend) ===
    path('whatsapp/conversazioni/',
         views.lista_conversazioni, name='wa-list'),
    path('whatsapp/conversazioni/<int:pk>/',
         views.dettaglio_conversazione, name='wa-detail'),
    path('whatsapp/conversazioni/<int:pk>/invia/',
         views.invia_messaggio, name='wa-send'),
    path('whatsapp/conversazioni/<int:pk>/segna-letti/',
         views.segna_letti, name='wa-read'),
    path('whatsapp/conversazioni/<int:pk>/aggancia-cliente/',
         views.aggancia_cliente, name='wa-link'),
    # Proxy media (audio/foto/video/document) - usa bearer token Meta
    path('whatsapp/media/<int:msg_id>/',
         views.media_proxy, name='wa-media'),

    # === Impianto IoT (MQTT) ===
    # Collaudo manuale moneta virtuale (staff only)
    path('test/moneta/', test_moneta, name='test-moneta'),
]
