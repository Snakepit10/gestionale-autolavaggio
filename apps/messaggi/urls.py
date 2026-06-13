from django.urls import path

from .views import MessaggiInboxView

app_name = 'messaggi'

urlpatterns = [
    path('', MessaggiInboxView.as_view(), name='inbox'),
]
