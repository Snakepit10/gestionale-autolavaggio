from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/postazione/(?P<postazione_id>\w+)/$', consumers.PostazioneConsumer.as_asgi()),
    re_path(r'ws/ordini/$', consumers.OrdiniConsumer.as_asgi()),
    re_path(r'ws/dashboard/$', consumers.DashboardConsumer.as_asgi()),
]