from django import template

from apps.personale.calcolo import testo_numero

register = template.Library()


@register.filter
def num(valore):
    """Numero senza zeri inutili e con la virgola: 170,5 / 10 / 1,17."""
    return testo_numero(valore)
