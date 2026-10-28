"""Notifica dei residui spazzole da vedere (scheda Distinta degli Ordini)."""
from datetime import date

from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag
def residui_da_vedere(user, giorno=None):
    """Lavaggi pagati agli operatori non abbinati e non visti del giorno
    (oggi se manca); 0 per chi non fa l'abbinamento spazzole."""
    from apps.finanze.services.abbinamento_portali import n_residui_da_vedere
    from apps.finanze.views import _chiusura_portali
    from apps.finanze.views_spazzole import fa_spazzole

    if not getattr(user, 'is_authenticated', False) or not fa_spazzole(user):
        return 0
    if not isinstance(giorno, date):
        giorno = timezone.localdate()
    return n_residui_da_vedere(_chiusura_portali(giorno)[0])
