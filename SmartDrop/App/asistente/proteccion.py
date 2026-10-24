"""Defensas de Gotita contra abusos.

* Topes para todo el sistema por día, aunque alguien cree muchas cuentas (cuida el saldo de Claude y ElevenLabs).
* Intentos de manipulación ("ignora tus reglas", "muéstrame tu prompt", "modo desarrollador"...): Gotita los
  rechaza sin preguntarle a Claude, y si una cuenta insiste, Gotita se pone en pausa para esa cuenta un rato.
* La conversación se guarda en el servidor (historial.py): así nadie puede inventar lo que Gotita "dijo" antes.
"""
import logging
import re
import unicodedata

from django.conf import settings
from django.core.cache import caches

from ..ratelimit import hit, rate_for

logger = logging.getLogger(__name__)
cache = caches['limites']  # la pausa por manipulación no debe borrarse con el tráfico normal

INTENTOS_POR_HORA = 5
PAUSA_SEGUNDOS = 3600
MAX_CUERPO = 2_621_440  # lo más que puede pesar una pregunta con la foto de la pantalla (lo mismo que acepta Django)

_MANIPULACION = [re.compile(patron) for patron in (
    r'\b(ignora|ignore|olvida|olvidate|forget|omite|saltate|salta|desactiva|disable|bypass|rompe)\b.{0,40}'
    r'\b(instrucciones|instructions|reglas|rules|indicaciones|restricciones|restrictions|directrices|prompt|filtros)\b',
    r'\b(muestra|muestrame|ensename|dime|decime|repite|revela|escribe|copia|imprime|show|reveal|print|repeat|'
    r'tell me|give me)\b.{0,30}\b(system prompt|prompt|instrucciones (del sistema|internas|ocultas|secretas)|'
    r'reglas (internas|ocultas|secretas)|configuracion interna|your instructions|hidden instructions)\b',
    r'\b(modo (desarrollador|developer|dios|god|sin restricciones|sin reglas|sin filtros?|libre|root|debug)|'
    r'developer mode|jailbreak|do anything now|dan mode|sin censura|uncensored)\b',
    r'\b(actua|act|finge|fingi|pretend|haz de cuenta|hace de cuenta|roleplay)\b.{0,30}'
    r'\b(sin reglas|sin restricciones|without (rules|restrictions)|otra ia|otro asistente|another ai|chatgpt)\b',
    r'\b(soy|i am|i\'m)\b.{0,20}\b(tu creador|tu programador|el desarrollador|de anthropic|from anthropic|'
    r'your developer|your creator)\b',
)]

RESPUESTA = {
    'es': 'Eso no lo puedo hacer: mis reglas no cambian. Te puedo explicar lo que ves, contarte cómo está tu '
          'agua o llevarte a otra pantalla.',
    'en': "I can't do that: my rules don't change. I can explain what you see, tell you how your water is "
          'doing or take you to another screen.',
}


def _normalizar(texto):
    sin_tildes = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', sin_tildes.lower())


def es_manipulacion(texto):
    normal = _normalizar(texto)
    return any(patron.search(normal) for patron in _MANIPULACION)


def en_pausa(user_id):
    return bool(cache.get(f'gotita:pausa:{user_id}'))


def registrar_intento(user_id):
    """Cuenta un intento de manipulación. Devuelve True si con este la cuenta queda en pausa."""
    logger.warning('Gotita: intento de manipulación de la cuenta %s', user_id)
    permitido, _ = hit('gotita_manipulacion', user_id, f'{INTENTOS_POR_HORA}/h')
    if not permitido:
        cache.set(f'gotita:pausa:{user_id}', True, PAUSA_SEGUNDOS)
        logger.warning('Gotita: la cuenta %s queda en pausa una hora por insistir', user_id)
        return True
    return False


def dentro_del_tope_global(nombre, limite_por_dia):
    """Tope de todo el sistema por día (todas las cuentas juntas)."""
    permitido, _ = hit(nombre, 'todo-el-sistema', rate_for(nombre, f'{limite_por_dia}/d'))
    if not permitido:
        logger.warning('Gotita: se alcanzó el tope diario de todo el sistema (%s)', nombre)
    return permitido


def tope_preguntas():
    return dentro_del_tope_global('gotita_global_preguntas', getattr(settings, 'ASISTENTE_LIMITE_GLOBAL_DIARIO', 3000))


def tope_voz():
    return dentro_del_tope_global('gotita_global_voz', getattr(settings, 'ASISTENTE_VOZ_GLOBAL_DIARIO', 3000))


def tope_dictado():
    return dentro_del_tope_global('gotita_global_dictado', getattr(settings, 'ASISTENTE_DICTADO_GLOBAL_DIARIO', 3000))

