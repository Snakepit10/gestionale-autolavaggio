from django import template

register = template.Library()


@register.filter
def abs_value(value):
    """Restituisce il valore assoluto di un numero"""
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value