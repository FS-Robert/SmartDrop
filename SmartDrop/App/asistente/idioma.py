"""Cambio de idioma automático de Gotita.

* Si está en español y le hablan en inglés, sigue en español; pero si le hacen más de 3 preguntas seguidas
  en inglés, o le dicen en inglés que no entienden español, se pasa sola a inglés.
* Si está en inglés y le hablan en español, se pasa a español de una vez (por si alguien lo activó sin querer).
"""
import re
import unicodedata

from django.core.cache import cache

PREGUNTAS_EN_INGLES = 3      # más de estas, seguidas, y se pasa a inglés
CUENTA_SEGUNDOS = 2 * 3600

_PALABRAS_EN = frozenset('''
the is are was were what whats how my your you does do did can could would should where why when which this
that it its please show tell i im dont doesnt cant water tank pressure quality level explain screen see open
take go to of and with there have has any alerts alert hello hi thanks thank read page here now today much
many about from for on at be been want need know help its whats hows wheres leak leaks supply usage am
'''.split())

_PALABRAS_ES = frozenset('''
el la los las que de del es esta estan mi mis tu tus como cuanto cuanta donde por para con una un y tanque
agua hay pantalla muestrame dime explicame quiero puedes ver sirve nivel presion calidad consumo hola gracias
ahorita chorro pila cayo ya se lo le al en eso esto yo son tambien muy bien mal pues vaya oye mira cual
hoy sale llega fuga fugas alertas avisos ayuda necesito porque cuando ella aqui alla ahi entiendo hablame
'''.split())

# "No entiendo español", dicho en inglés: se cambia de una vez.
_PIDE_INGLES = re.compile(
    r"\b(?:i\s+(?:do\s*n[o']?t|dont|can\s*n?[o']?t|cant)\s+(?:understand|speak|read)\s+spanish|"
    r"no\s+spanish|(?:in\s+)?english\s+please|please\s+(?:speak|talk|answer)\s+(?:in\s+)?english|"
    r"(?:speak|talk|answer)\s+(?:to\s+me\s+)?in\s+english|i\s+only\s+speak\s+english)\b")


def _normalizar(texto):
    sin_tildes = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r"[^a-z\s']", ' ', sin_tildes.lower())


def detectar(texto):
    """'es', 'en' o None si no se nota (por ejemplo "ok" o solo "Gotita")."""
    crudo = str(texto or '')
    palabras = [p.replace("'", '') for p in _normalizar(crudo).split()]
    palabras = [p for p in palabras if p != 'gotita']
    es = sum(p in _PALABRAS_ES for p in palabras)
    en = sum(p in _PALABRAS_EN for p in palabras)
    if re.search(r'[ñ¿¡áéíóú]', crudo.lower()):
        es += 2
    mayor, menor = max(es, en), min(es, en)
    if mayor == 0 or mayor == menor:
        return None
    if mayor < 2 and len(palabras) > 3:
        return None
    return 'es' if es > en else 'en'


def pide_ingles(texto):
    return bool(_PIDE_INGLES.search(_normalizar(texto)))


def _clave(user_id, plataforma):
    return f'gotita:preguntas_en_ingles:{plataforma}:{user_id}'


def decidir(user_id, plataforma, actual, texto, hablado=None):
    """Idioma con el que Gotita va a responder esta pregunta. Si cambia, la web o la app mueven el interruptor.

    hablado: el idioma que detectó el dictado de ElevenLabs al oír la voz (es más seguro que adivinar por el texto).
    """
    clave = _clave(user_id, plataforma)
    detectado = hablado or detectar(texto)
    if actual == 'en':
        if detectado == 'es':
            cache.delete(clave)
            return 'es'
        return 'en'
    if pide_ingles(texto):
        cache.delete(clave)
        return 'en'
    if detectado == 'en':
        seguidas = (cache.get(clave) or 0) + 1
        if seguidas > PREGUNTAS_EN_INGLES:
            cache.delete(clave)
            return 'en'
        cache.set(clave, seguidas, CUENTA_SEGUNDOS)
    elif detectado == 'es':
        cache.delete(clave)
    return actual
