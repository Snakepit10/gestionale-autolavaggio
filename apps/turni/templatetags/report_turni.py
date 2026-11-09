from django import template

register = template.Library()


@register.filter
def durata(minuti):
    """Minuti in testo leggibile: 4,5 min, 23 min, 1 h 05."""
    if minuti is None:
        return '—'
    minuti = float(minuti)
    if minuti < 10:
        return f'{minuti:.1f} min'.replace('.', ',')
    if minuti < 60:
        return f'{minuti:.0f} min'
    totale = round(minuti)
    return f'{totale // 60} h {totale % 60:02d}'


@register.filter
def ore(valore):
    """Ore decimali in testo: 12 h 30."""
    if valore is None:
        return '—'
    return durata(float(valore) * 60)
