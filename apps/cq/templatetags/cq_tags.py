import json
from django import template

register = template.Library()


@register.filter
def in_group(user, group_name):
    """Verifica se l'utente appartiene al gruppo indicato. Uso: {{ user|in_group:'titolare' }}"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=group_name).exists()


@register.filter
def can_compile_cq(user):
    """True se l'utente può compilare schede CQ (responsabile o titolare)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['responsabile', 'titolare']).exists()


@register.filter
def zip_lists(a, b):
    return zip(a, b)


@register.filter
def abs_value(value):
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value


@register.filter
def segno(value):
    try:
        return f'+{value}' if value >= 0 else str(value)
    except (TypeError, ValueError):
        return value


@register.filter
def indice_badge_class(indice):
    if indice is None:
        return 'bg-secondary'
    if indice > 0:
        return 'bg-success'
    if indice < 0:
        return 'bg-danger'
    return 'bg-secondary'


@register.filter
def zona_json(zona):
    """Serializza un ZonaConfig in JSON per i modal di modifica."""
    return json.dumps({
        'nome': zona.nome,
        'codice': zona.codice,
        'ordine': zona.ordine,
        'categoria_id': zona.categoria_id,
        'postazione_produttore': zona.postazione_produttore,
        'postazioni_catena': zona.postazioni_catena or [],
        'attiva': zona.attiva,
    })


@register.filter
def tipo_json(tipo):
    """Serializza un TipoDifettoConfig in JSON per i modal di modifica."""
    return json.dumps({
        'nome': tipo.nome,
        'codice': tipo.codice,
        'ordine': tipo.ordine,
        'categoria_id': tipo.categoria_id,
        'richiede_descrizione': tipo.richiede_descrizione,
        'attivo': tipo.attivo,
    })
