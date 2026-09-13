import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter

def resaltar(texto, busqueda):
    """Highlight every case-insensitive occurrence of a search term."""
    if not texto or not busqueda:
        return texto

    texto_escapado = conditional_escape(str(texto))
    busqueda_escapada = re.escape(str(busqueda))
    patron = re.compile(busqueda_escapada, re.IGNORECASE)
    marcado = patron.sub(
        lambda coincidencia: (
            '<mark class="bg-warning text-dark px-1 rounded">'
            f'{coincidencia.group(0)}'
            '</mark>'
        ),
        texto_escapado,
    )
    return mark_safe(marcado)
