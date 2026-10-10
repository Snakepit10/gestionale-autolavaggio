"""View staff per la pagina inbox WhatsApp.

Render del template /messaggi/. Tutti i dati operativi (conversazioni,
storia, invio risposta) viaggiano via REST API in apps/api/views.py.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import TemplateView


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Chi ha la sezione Messaggi (Configurazione > Permessi)."""
    def test_func(self):
        from apps.auth_system.sezioni import ha_accesso
        return ha_accesso(self.request.user, 'messaggi')


class MessaggiInboxView(StaffRequiredMixin, TemplateView):
    template_name = 'messaggi/inbox.html'
