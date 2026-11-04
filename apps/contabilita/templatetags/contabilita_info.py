"""{% info "Nome" %}: l'icona con la casella che spiega l'indicatore. Si apre
passando il mouse sulla casella dell'indicatore, o toccandola dal telefono
(script in contabilita/_stile.html). Niente, se il nome non ha spiegazione."""
from django import template
from django.utils.html import escape, format_html, format_html_join

from ..spiegazioni import spiegazione

register = template.Library()

TITOLI = ('Come si calcola', 'Cosa indica', 'Attenzione')


@register.simple_tag
def info(nome):
    testi = spiegazione(nome)
    if not testi:
        return ''
    contenuto = format_html_join('', '<div class="mb-1"><b>{}</b><br>{}</div>', zip(TITOLI, testi))
    return format_html('<i class="bi bi-info-circle info-kpi" data-info-titolo="{}" data-info-testo="{}"></i>',
                       nome, escape(contenuto))           # e' HTML, ma qui sta dentro un attributo
