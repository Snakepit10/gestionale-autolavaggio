"""View staff per la pagina inbox WhatsApp.

Render del template /messaggi/. Tutti i dati operativi (conversazioni,
storia, invio risposta) viaggiano via REST API in apps/api/views.py.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import TemplateView


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class MessaggiInboxView(StaffRequiredMixin, TemplateView):
    template_name = 'messaggi/inbox.html'
