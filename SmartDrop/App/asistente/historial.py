"""Historial de conversaciones con Gotita.

Cada persona ve solo sus conversaciones, en la web y en la app (son las mismas).
Una conversación que lleva 20 días sin usarse se borra sola, para no llenar la base de datos.
Lo que Gotita recuerda al responder sale de aquí, no de lo que mande el navegador o la app.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from ..models import ConversacionGotita, MensajeGotita

logger = logging.getLogger(__name__)

DIAS = 20
MAX_LISTA = 50
MAX_MENSAJES = 200          # lo más que se muestra al abrir una conversación
MEMORIA = 8                 # mensajes que Gotita recuerda al responder
MAX_TEXTO = 2000
MAX_TITULO = 60
SEGUIR_MINUTOS = 30         # si la app o la web no dicen qué conversación es, se sigue la última de hace poco


def _vencimiento():
    return timezone.now() - timedelta(days=DIAS)


def limpiar_viejas(forzar=False):
    """Borra las conversaciones de todos que llevan más de 20 días sin usarse (como mucho una vez por hora)."""
    if not forzar and not cache.add('gotita:limpieza', True, 3600):
        return 0
    borradas, _ = ConversacionGotita.objects.filter(actualizada__lt=_vencimiento()).delete()
    if borradas:
        logger.info('Gotita: se borraron %s registros viejos del historial', borradas)
    return borradas


def _de(id_usuario):
    return ConversacionGotita.objects.filter(id_usuario=id_usuario, actualizada__gte=_vencimiento())


def _id_valido(valor):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def buscar(id_usuario, conversacion_id):
    """La conversación si es de esta persona y sigue vigente; si no, None."""
    numero = _id_valido(conversacion_id)
    if numero is None or not id_usuario:
        return None
    return _de(id_usuario).filter(pk=numero).first()


def la_de_ahora(id_usuario, plataforma, conversacion_id, sigue):
    """Qué conversación continúa esta pregunta.

    * Si la app o la web mandan el número de la conversación, es esa (si es de la persona).
    * Si no lo mandan pero el chat ya tenía mensajes, se sigue la última de los últimos 30 minutos.
    * Si no, la pregunta empieza una conversación nueva (se crea al guardar la respuesta).
    """
    if conversacion_id is not None:
        return buscar(id_usuario, conversacion_id)
    if sigue and id_usuario:
        hace_poco = timezone.now() - timedelta(minutes=SEGUIR_MINUTOS)
        return _de(id_usuario).filter(plataforma=plataforma, actualizada__gte=hace_poco).first()
    return None


def memoria(conversacion):
    """Lo último que se habló en la conversación, en el formato que entiende Claude."""
    if conversacion is None:
        return []
    ultimos = list(conversacion.mensajes.order_by('-id')[:MEMORIA])[::-1]
    return [('assistant' if m.rol == 'gotita' else 'user', m.texto[:800]) for m in ultimos]


def _titulo(pregunta):
    texto = ' '.join(str(pregunta or '').split())
    if len(texto) <= MAX_TITULO:
        return texto or 'Conversación'
    corte = texto.rfind(' ', 0, MAX_TITULO)
    return texto[:corte if corte > 20 else MAX_TITULO].rstrip(' ,.;:') + '…'


def guardar(id_usuario, plataforma, conversacion, pregunta, respuesta):
    """Guarda la pregunta y la respuesta. Devuelve la conversación (nueva si no había)."""
    if not id_usuario:
        return None
    if conversacion is None:
        conversacion = ConversacionGotita.objects.create(
            id_usuario=id_usuario, plataforma=plataforma, titulo=_titulo(pregunta))
    MensajeGotita.objects.bulk_create([
        MensajeGotita(conversacion=conversacion, rol='user', texto=str(pregunta)[:MAX_TEXTO]),
        MensajeGotita(conversacion=conversacion, rol='gotita', texto=str(respuesta)[:MAX_TEXTO]),
    ])
    conversacion.save(update_fields=['actualizada'])
    return conversacion


def lista(id_usuario):
    limpiar_viejas()
    vencimiento = _vencimiento()
    filas = _de(id_usuario).only('id', 'titulo', 'plataforma', 'actualizada')[:MAX_LISTA]
    return [{
        'id': c.id,
        'titulo': c.titulo,
        'plataforma': c.plataforma,
        'actualizada': c.actualizada.isoformat(),
        # Días que le quedan antes de borrarse sola.
        'quedan_dias': max(0, (c.actualizada - vencimiento).days),
    } for c in filas]


def mensajes(id_usuario, conversacion_id):
    conversacion = buscar(id_usuario, conversacion_id)
    if conversacion is None:
        return None
    filas = conversacion.mensajes.order_by('-id')[:MAX_MENSAJES]
    return {
        'id': conversacion.id,
        'titulo': conversacion.titulo,
        'mensajes': [{'rol': m.rol, 'texto': m.texto, 'hora': m.creado.isoformat()} for m in reversed(filas)],
    }


def borrar(id_usuario, conversacion_id):
    conversacion = buscar(id_usuario, conversacion_id)
    if conversacion is None:
        return False
    conversacion.delete()
    return True


def borrar_todo(id_usuario):
    if not id_usuario:
        return 0
    borradas, _ = ConversacionGotita.objects.filter(id_usuario=id_usuario).delete()
    return borradas
