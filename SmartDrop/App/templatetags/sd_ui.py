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


def _a_hora_local(valor):
    """Fecha ISO (o datetime) en la zona horaria del proyecto (America/El_Salvador); None si no es fecha."""
    from datetime import datetime

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    if not valor:
        return None
    fecha = valor if isinstance(valor, datetime) else None
    if fecha is None:
        try:
            fecha = parse_datetime(str(valor).strip())
        except ValueError:
            return None
    if fecha is None:
        return None
    return timezone.localtime(fecha) if timezone.is_aware(fecha) else fecha


@register.filter
def fecha_hora_local(valor):
    """'2026-10-22T04:32:28+00:00' -> '21/10/2026 22:32:28' (hora de El Salvador). Si no es fecha, igual."""
    fecha = _a_hora_local(valor)
    return f'{fecha:%d/%m/%Y %H:%M:%S}' if fecha else valor


@register.filter
def hora_local(valor):
    """'2026-10-22T04:32:28+00:00' -> '22:32:28' (hora de El Salvador). Si no es fecha, igual."""
    fecha = _a_hora_local(valor)
    return f'{fecha:%H:%M:%S}' if fecha else valor


@register.filter
def duracion_texto(segundos):
    """95 -> '1 min 35 s'; 3700 -> '1 h 1 min'. Vacío si no es un número."""
    try:
        total = int(float(segundos))
    except (TypeError, ValueError):
        return ''
    if total < 60:
        return f'{total} s'
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f'{horas} h {minutos} min' if minutos else f'{horas} h'
    return f'{minutos} min {segs} s' if segs else f'{minutos} min'


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
