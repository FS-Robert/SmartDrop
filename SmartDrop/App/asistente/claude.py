"""Aquí Gotita le pregunta a Claude y recibe la respuesta, y también escribe los avisos cortos.

La llave de Claude solo se usa aquí, en el servidor; nunca llega a la web ni a la app.
Gotita solo puede hacer estas cosas: cambiar de pantalla, mover la vista a una parte de la pantalla,
cambiar el modo de explicar, el idioma y la velocidad de su voz. No puede guardar datos, abrir o cerrar sesiones ni mover la válvula.
"""
import logging
import os

import anthropic
from django.conf import settings
from django.utils import timezone

from . import destinos, prompts
from .seguridad import AVISO_CODIGO, FiltroSalida, filtrar_texto

logger = logging.getLogger(__name__)

# Si Claude no quiere responder algo, Anthropic prueba con otro modelo para no dejar a la persona sin respuesta.
BETA_FALLBACK = 'server-side-fallback-2026-07-01'
MAX_TOKENS_TURNO = 2048
MAX_TOKENS_AVISO = 600
TIMEOUT_TURNO = 45.0
TIMEOUT_AVISO = 15.0
MAX_AVISO = 280

MODOS = ('sencillo', 'tecnico')
VELOCIDADES = ('lenta', 'normal')
IDIOMAS = ('es', 'en')

MENSAJES = {
    'es': {
        'rechazo': 'Con eso no te puedo ayudar, pero puedo explicarte lo que ves o llevarte a otra pantalla.',
        'vacio': 'No te entendí bien. ¿Me lo repites con otras palabras?',
        'navegar': 'Te llevo a {destino}.',
        'seccion': 'Mira, aquí está {seccion}.',
        'modo_tecnico': 'Listo, te explico en modo técnico.',
        'modo_sencillo': 'Listo, te explico en modo sencillo.',
        'velocidad_lenta': 'Listo, hablaré más despacio.',
        'velocidad_normal': 'Listo, vuelvo a la velocidad normal.',
        'idioma_es': 'Listo, ahora te hablo en español.',
        'idioma_en': "Done, I'll speak English now.",
        'sin_conexion': 'No me pude conectar en este momento. Revisa tu internet e inténtalo de nuevo.',
        'ocupada': 'Estoy atendiendo muchas preguntas. Inténtalo en un momento.',
        'error': 'Tuve un problema para responder. Inténtalo de nuevo en un momento.',
    },
    'en': {
        'rechazo': "I can't help with that, but I can explain what you see or take you to another screen.",
        'vacio': "I didn't quite get that. Could you say it another way?",
        'navegar': 'Taking you to {destino}.',
        'seccion': 'Here is {seccion}.',
        'modo_tecnico': "Done, I'll explain things in technical mode.",
        'modo_sencillo': "Done, I'll explain things in simple mode.",
        'velocidad_lenta': "Done, I'll speak more slowly.",
        'velocidad_normal': "Done, back to normal speed.",
        'idioma_es': 'Listo, ahora te hablo en español.',
        'idioma_en': "Done, I'll speak English now.",
        'sin_conexion': "I couldn't connect right now. Check your internet and try again.",
        'ocupada': "I'm handling a lot of questions. Please try again in a moment.",
        'error': 'I had a problem answering. Please try again in a moment.',
    },
}


def _llave():
    return os.environ.get('ANTHROPIC_API_KEY', '').strip()


def disponible():
    return bool(getattr(settings, 'ASISTENTE_ACTIVO', False)) and bool(_llave())


def texto(idioma, clave, **valores):
    return MENSAJES.get(idioma, MENSAJES['es'])[clave].format(**valores)


def _herramienta(nombre, descripcion, campo, opciones):
    # Lo que pida Claude se revisa otra vez en acciones_de: solo pasan las opciones de la lista.
    return {
        'name': nombre,
        'description': descripcion,
        'input_schema': {
            'type': 'object',
            'properties': {campo: {'type': 'string', 'enum': list(opciones)}},
            'required': [campo],
            'additionalProperties': False,
        },
    }


def herramientas(rol, plataforma):
    return [
        _herramienta('navegar', 'Abre otra pantalla de SmartDrop. Úsala solo si el usuario pide ir, abrir, mostrar '
                     'o ver una sección.', 'destino', destinos.disponibles(rol, plataforma)),
        {
            # La lista de partes cambia en cada pantalla, por eso no va aquí: llega con la pregunta y se revisa
            # en acciones_de (solo pasan las que mandó la web o la app).
            'name': 'mostrar_seccion',
            'description': 'Mueve la vista a una parte de la pantalla que el usuario ya tiene abierta y la resalta. '
                           'Usa solo un id de los que vienen en <secciones>.',
            'input_schema': {
                'type': 'object',
                'properties': {'seccion': {'type': 'string', 'maxLength': 40}},
                'required': ['seccion'],
                'additionalProperties': False,
            },
        },
        _herramienta('cambiar_modo', 'Cambia cómo explicas: "sencillo" (palabras de todos los días) o "tecnico" '
                     '(con unidades y términos).', 'modo', MODOS),
        _herramienta('cambiar_velocidad', 'Cambia la velocidad de tu voz: "lenta" o "normal".', 'velocidad',
                     VELOCIDADES),
        _herramienta('cambiar_idioma', 'Cambia el idioma en que hablas y respondes: "es" (español) o "en" '
                     '(inglés). Úsala cuando el usuario te pida hablar en otro idioma.', 'idioma', IDIOMAS),
    ]


def _ahora_texto():
    return timezone.localtime().strftime('%d/%m/%Y %H:%M')


def mensajes_para(turno, datos, nombre=''):
    """Lo último que se habló, más la pregunta nueva con lo que hay en pantalla, los datos y la foto si la hay."""
    mensajes = [{'role': rol, 'content': contenido} for rol, contenido in turno['historial']]
    while mensajes and mensajes[0]['role'] != 'user':
        mensajes.pop(0)
    contenido = []
    if turno['captura']:
        media_type, datos_b64 = turno['captura']
        contenido.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': datos_b64}})
    contenido.append({'type': 'text', 'text': prompts.mensaje_turno(turno, datos, _ahora_texto(), nombre)})
    mensajes.append({'role': 'user', 'content': contenido})
    return mensajes


def acciones_de(bloques, rol, plataforma, secciones=()):
    """Lo que Claude pidió hacer, revisado aquí: lo que no está permitido para la persona se descarta."""
    secciones = dict(secciones)
    acciones = []
    for bloque in bloques or []:
        if getattr(bloque, 'type', None) != 'tool_use' or not isinstance(bloque.input, dict):
            continue
        if bloque.name == 'navegar':
            destino = bloque.input.get('destino')
            if destinos.permitido(destino, rol, plataforma):
                accion = {'tipo': 'navegar', 'destino': destino, 'nombre': destinos.nombre(destino)}
                if plataforma == destinos.WEB:
                    accion['url'] = destinos.url_web(destino)
                acciones.append(accion)
        elif bloque.name == 'mostrar_seccion' and str(bloque.input.get('seccion')) in secciones:
            seccion = bloque.input['seccion']
            acciones.append({'tipo': 'seccion', 'seccion': seccion, 'nombre': secciones[seccion]})
        elif bloque.name == 'cambiar_modo' and bloque.input.get('modo') in MODOS:
            acciones.append({'tipo': 'modo', 'modo': bloque.input['modo']})
        elif bloque.name == 'cambiar_velocidad' and bloque.input.get('velocidad') in VELOCIDADES:
            acciones.append({'tipo': 'velocidad', 'velocidad': bloque.input['velocidad']})
        elif bloque.name == 'cambiar_idioma' and bloque.input.get('idioma') in IDIOMAS:
            acciones.append({'tipo': 'idioma', 'idioma': bloque.input['idioma']})
    return acciones


def _frase_de_accion(accion, idioma):
    if accion['tipo'] == 'navegar':
        return texto(idioma, 'navegar', destino=accion['nombre'])
    if accion['tipo'] == 'seccion':
        return texto(idioma, 'seccion', seccion=accion['nombre'])
    if accion['tipo'] == 'modo':
        return texto(idioma, f'modo_{accion["modo"]}')
    if accion['tipo'] == 'idioma':
        return texto(accion['idioma'], f'idioma_{accion["idioma"]}')
    return texto(idioma, f'velocidad_{accion["velocidad"]}')


def _cliente_async():
    return anthropic.AsyncAnthropic(api_key=_llave(), timeout=TIMEOUT_TURNO, max_retries=1)


async def conversar(turno, rol, plataforma, idioma, datos, usuario_log='', nombre=''):
    """Le pregunta a Claude y va entregando la respuesta frase por frase (ya revisada), más las acciones.

    Así la app y la web empiezan a hablar sin esperar a que Gotita termine toda la respuesta.
    """
    filtro = FiltroSalida(idioma)
    frases = 0
    acciones = []
    final = None
    try:
        async with _cliente_async() as cliente:
            async with cliente.beta.messages.stream(
                model=settings.ASISTENTE_MODELO,
                max_tokens=MAX_TOKENS_TURNO,
                system=[{'type': 'text', 'text': prompts.sistema(rol, plataforma, idioma),
                         'cache_control': {'type': 'ephemeral'}}],
                tools=herramientas(rol, plataforma),
                messages=mensajes_para(turno, datos, nombre),
                output_config={'effort': 'low'},
                betas=[BETA_FALLBACK],
                fallbacks='default',
            ) as stream:
                async for evento in stream:
                    if evento.type == 'text':
                        for frase in filtro.agregar(evento.text):
                            frases += 1
                            yield {'evento': 'texto', 't': frase}
                    elif evento.type == 'content_block_stop' and evento.content_block.type == 'tool_use':
                        # Si pide cambiar de pantalla o de modo, se avisa de una vez, sin esperar el final.
                        secciones = turno['pantalla'].get('secciones') or ()
                        for accion in acciones_de([evento.content_block], rol, plataforma, secciones):
                            for frase in filtro.cerrar():
                                frases += 1
                                yield {'evento': 'texto', 't': frase}
                            if not frases:
                                frases += 1
                                yield {'evento': 'texto', 't': _frase_de_accion(accion, idioma)}
                            acciones.append(accion)
                            yield {'evento': 'accion', **accion}
                final = await stream.get_final_message()
    except anthropic.RateLimitError:
        logger.warning('Gotita: límite de Anthropic alcanzado')
        yield {'evento': 'error', 'mensaje': texto(idioma, 'ocupada')}
        return
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        logger.warning('Gotita: sin conexión con Anthropic')
        yield {'evento': 'error', 'mensaje': texto(idioma, 'sin_conexion')}
        return
    except anthropic.APIStatusError as exc:
        logger.error('Gotita: error de la API de Anthropic (%s)', exc.status_code)
        yield {'evento': 'error', 'mensaje': texto(idioma, 'error')}
        return

    for frase in filtro.cerrar():
        frases += 1
        yield {'evento': 'texto', 't': frase}

    if final is not None and final.stop_reason == 'refusal':
        yield {'evento': 'texto', 't': texto(idioma, 'rechazo')}
        yield {'evento': 'fin'}
        return

    if not frases:
        yield {'evento': 'texto', 't': texto(idioma, 'vacio')}

    uso = getattr(final, 'usage', None)
    logger.info(
        'Gotita: usuario=%s rol=%s plataforma=%s entrada=%s cache=%s salida=%s acciones=%s',
        usuario_log, rol, plataforma, getattr(uso, 'input_tokens', '?'),
        getattr(uso, 'cache_read_input_tokens', '?'), getattr(uso, 'output_tokens', '?'),
        ','.join(a['tipo'] for a in acciones) or '-',
    )
    yield {'evento': 'fin'}


def redactar_aviso(aviso, rol, idioma):
    """Texto corto de Gotita para una notificación. None si Gotita está apagada o algo falla (se usa el original)."""
    if not disponible():
        return None
    try:
        with anthropic.Anthropic(api_key=_llave(), timeout=TIMEOUT_AVISO, max_retries=1) as cliente:
            respuesta = cliente.beta.messages.create(
                model=settings.ASISTENTE_MODELO,
                max_tokens=MAX_TOKENS_AVISO,
                system=prompts.sistema_avisos(rol, idioma),
                messages=[{'role': 'user', 'content': prompts.mensaje_aviso(aviso)}],
                output_config={'effort': 'low'},
                betas=[BETA_FALLBACK],
                fallbacks='default',
            )
    except anthropic.APIError:
        logger.warning('Gotita: no se pudo redactar un aviso', exc_info=True)
        return None
    if respuesta.stop_reason == 'refusal':
        return None
    crudo = ' '.join(b.text for b in respuesta.content if getattr(b, 'type', None) == 'text')
    limpio = filtrar_texto(crudo, idioma)
    if not limpio or limpio == AVISO_CODIGO.get(idioma, AVISO_CODIGO['es']):
        return None
    return limpio[:MAX_AVISO].rstrip()
