"""Ayudantes de presentación para las plantillas (sistema de diseño "Agua de la zona").

Un solo vocabulario de estado en toda la interfaz: ok, warn, bad, none, info. Estos filtros traducen
los estados que ya entregan las vistas (badge_class, estado_amigable, estado de reportes, válvulas…)
a ese vocabulario, para usarlo como modificador CSS: class="status status--{{ x|estado_css }}".
"""
from django import template

register = template.Library()

_OK = {
    'normal', 'estable', 'lleno', 'maximo', 'máximo', 'buena', 'bueno', 'optima', 'óptima', 'excelente',
    'resuelto', 'resuelta', 'open', 'abierta', 'activo', 'activa', 'en linea', 'en línea', 'online',
    'disponible', 'badge-normal', 'badge-perfect', 'badge-open', 'ok', 'bajo_riesgo', 'baja_prob',
}
_WARN = {
    'baja', 'bajo', 'alta', 'media', 'medio', 'aceptable', 'pendiente', 'no_confirmada', 'atencion',
    'atención', 'moderado', 'moderada', 'badge-high', 'warn', 'riesgo_medio',
}
_BAD = {
    'muy_alta', 'muy alta', 'critico', 'crítico', 'critica', 'crítica', 'mala', 'malo', 'revisar',
    'confirmada', 'fuga', 'error', 'falla', 'badge-closed', 'bad', 'riesgo_alto', 'alto',
}
_INFO = {'en_proceso', 'en proceso', 'en_revision', 'en revisión', 'info'}


def _norm(valor):
    return str(valor or '').strip().lower()


@register.filter
def estado_css(valor):
    """Modificador de estado (ok/warn/bad/info/none) para un estado o clase de insignia de las vistas."""
    v = _norm(valor)
    if v in _OK:
        return 'ok'
    if v in _WARN:
        return 'warn'
    if v in _BAD:
        return 'bad'
    if v in _INFO:
        return 'info'
    return 'none'


@register.filter
def sentence(valor):
    """'ABIERTA' -> 'Abierta', 'SIN DATOS' -> 'Sin datos': textos de las vistas sin mayúsculas sostenidas."""
    texto = str(valor or '').strip()
    if not texto:
        return texto
    return texto[:1].upper() + texto[1:].lower() if texto.isupper() else texto


_MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


@register.filter
def fecha_corta(valor):
    """'2026-10-07T16:40:09+00:00' -> '7 oct 2026, 10:40' (hora local). Si no es fecha, se devuelve igual."""
    from datetime import datetime

    from django.utils import timezone
    from django.utils.dateparse import parse_date, parse_datetime

    if not valor:
        return valor
    fecha = valor if isinstance(valor, datetime) else None
    if fecha is None:
        texto = str(valor).strip()
        try:
            fecha = parse_datetime(texto)
        except ValueError:
            fecha = None
        if fecha is None:
            try:
                solo_dia = parse_date(texto)
            except ValueError:
                solo_dia = None
            if solo_dia is None:
                return valor
            return f'{solo_dia.day} {_MESES[solo_dia.month - 1]} {solo_dia.year}'
    if timezone.is_aware(fecha):
        fecha = timezone.localtime(fecha)
    return f'{fecha.day} {_MESES[fecha.month - 1]} {fecha.year}, {fecha:%H:%M}'


@register.simple_tag
def resumen_hogar(*estados):
    """Resumen en una frase del agua de la zona a partir de varios estados.

    Devuelve {'mod': ok|warn|bad|none, 'texto': frase} con el peor estado conocido.
    """
    mods = [estado_css(e) for e in estados]
    conocidos = [m for m in mods if m != 'none']
    if not conocidos:
        return {'mod': 'none', 'texto': 'Aún no tenemos lecturas de tu zona.'}
    if 'bad' in conocidos:
        return {'mod': 'bad', 'texto': 'Hay algo que revisar en el agua de tu zona.'}
    if 'warn' in conocidos:
        return {'mod': 'warn', 'texto': 'Tu agua funciona, con un detalle a tener en cuenta.'}
    return {'mod': 'ok', 'texto': 'Tu agua está en buen estado.'}
