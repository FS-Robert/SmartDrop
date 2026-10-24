"""Direcciones del servidor que usa Gotita.

Web:  /asistente/turno/ (hacer una pregunta), /asistente/avisos/ (avisos nuevos),
      /asistente/dictado/ (pasar a texto una pregunta dicha) y /asistente/conversaciones/ (historial).
App:  /api/asistente/turno/ (hacer una pregunta), /api/asistente/ (si Gotita está encendida) y
      /api/asistente/conversaciones/ (historial).

La respuesta a una pregunta llega frase por frase, junto con lo que Gotita quiera hacer (cambiar de
pantalla, de modo, de idioma o de velocidad). Si algo impide preguntar (sin sesión, demasiadas preguntas,
pregunta vacía o Gotita apagada) se responde con un mensaje de error.
"""
import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from ..api.permissions import IsAuthenticatedUser
from ..queries import owner_id
from ..ratelimit import enabled, hit, rate_for
from . import avisos, claude, contexto, destinos, historial, idioma as idiomas, prompts, proteccion, voz
from .seguridad import EntradaInvalida, limpiar_turno

logger = logging.getLogger(__name__)

LIMITE_MINUTO = '12/m'
VOZ_POR_MINUTO = '40/m'
DICTADO_POR_MINUTO = '15/m'
MAX_AUDIO = 3 * 1024 * 1024   # unos 2 minutos de voz; una pregunta normal pesa mucho menos
MIN_AUDIO = 1000
# Cómo empieza cada tipo de audio permitido (para no mandarle a ElevenLabs algo que no es audio).
_TIPOS_AUDIO = {
    'audio/webm': lambda a: a.startswith(b'\x1aE\xdf\xa3'),
    'audio/ogg': lambda a: a.startswith(b'OggS'),
    'audio/mp4': lambda a: a[4:8] == b'ftyp',
    'audio/wav': lambda a: a.startswith(b'RIFF') and a[8:12] == b'WAVE',
}


def _error(status, mensaje, retry_after=None):
    response = JsonResponse({'ok': False, 'error': mensaje}, status=status)
    if retry_after:
        response['Retry-After'] = str(retry_after)
    return response


async def _sse(eventos):
    yield ': gotita\n\n'  # se manda algo de una vez para que la respuesta empiece a llegar
    async for evento in eventos:
        tipo = evento.pop('evento')
        yield f'event: {tipo}\ndata: {json.dumps(evento, ensure_ascii=False)}\n\n'


def _limites(user_id):
    """Revisa que la persona no pregunte demasiado seguido ni pase el límite del día (así se cuida el gasto)."""
    if enabled():
        permitida, espera = hit('asistente_minuto', user_id, rate_for('asistente_minuto', LIMITE_MINUTO))
        if not permitida:
            return _error(429, f'Vas muy rápido. Espera {espera} segundos y vuelve a preguntar.', espera)
    limite_dia = rate_for('asistente_dia', f'{settings.ASISTENTE_LIMITE_DIARIO}/d')
    permitida, espera = hit('asistente_dia', user_id, limite_dia)
    if not permitida:
        return _error(429, 'Llegaste al límite de preguntas de hoy. Mañana seguimos.', espera)
    return None


async def _con_cambio_de_idioma(eventos, nuevo):
    """Primero le dice a la web o la app que mueva el interruptor de idioma, luego la respuesta."""
    yield {'evento': 'accion', 'tipo': 'idioma', 'idioma': nuevo, 'automatico': True}
    async for evento in eventos:
        yield evento


async def _solo_decir(texto):
    """Respuesta fija de Gotita, sin preguntarle a Claude."""
    yield {'evento': 'texto', 't': texto}
    yield {'evento': 'fin'}


async def _con_memoria(eventos, id_usuario, plataforma, conversacion, pregunta):
    """Pasa la respuesta tal cual y, al final, la guarda en el historial y avisa el número de la conversación."""
    partes = []
    async for evento in eventos:
        if evento.get('evento') == 'texto':
            partes.append(evento.get('t', ''))
        elif evento.get('evento') == 'fin' and partes:
            try:
                guardada = await sync_to_async(historial.guardar)(
                    id_usuario, plataforma, conversacion, pregunta, ' '.join(partes))
            except Exception:  # Si no se pudo guardar, la respuesta igual llega.
                logger.warning('Gotita: no se pudo guardar la conversación', exc_info=True)
                guardada = None
            if guardada is not None:
                yield {'evento': 'conversacion', 'id': guardada.id, 'titulo': guardada.titulo}
        yield evento


def _respuesta_por_frases(eventos):
    response = StreamingHttpResponse(_sse(eventos), content_type='text/event-stream; charset=utf-8')
    response['Cache-Control'] = 'no-cache, no-transform'
    # Que cada frase llegue apenas sale, sin guardarla ni comprimirla en el camino
    # (si se comprime, el navegador solo alcanza a leer la primera frase).
    response['X-Accel-Buffering'] = 'no'
    response['Content-Encoding'] = 'identity'
    return response


def responder_turno(user, data, plataforma):
    if not claude.disponible():
        return _error(503, 'Gotita está apagada por ahora.')
    user_id = str(owner_id(user) or 'anonimo')
    if proteccion.en_pausa(user_id):
        return _error(429, 'Gotita está en pausa para tu cuenta por un rato.')
    bloqueo = _limites(user_id)
    if bloqueo is not None:
        return bloqueo
    try:
        turno = limpiar_turno(data)
    except EntradaInvalida as exc:
        return _error(400, str(exc))

    elegido = turno['idioma'] or avisos.idioma_de(user)
    # Si le hablan en otro idioma, Gotita puede pasarse sola (ver idioma.py).
    idioma = idiomas.decidir(user_id, plataforma, elegido, turno['texto'], hablado=turno['idioma_hablado'])
    if proteccion.es_manipulacion(turno['texto']):
        # Ni siquiera se le pregunta a Claude; si la cuenta insiste, queda en pausa un rato.
        if proteccion.registrar_intento(user_id):
            return _error(429, 'Gotita está en pausa para tu cuenta por un rato.')
        return _respuesta_por_frases(_solo_decir(proteccion.RESPUESTA.get(idioma, proteccion.RESPUESTA['es'])))
    if not proteccion.tope_preguntas():
        return _error(503, 'Gotita descansa por hoy. Mañana seguimos.')

    # Lo que se habló antes sale del historial del servidor, no de lo que mande el navegador o la app.
    id_usuario = owner_id(user)
    conversacion = historial.la_de_ahora(id_usuario, plataforma, turno['conversacion'], turno['sigue'])
    turno['historial'] = historial.memoria(conversacion)

    rol = destinos.rol_de(user)
    datos = contexto.datos_de(user, rol)
    eventos = claude.conversar(turno, rol, plataforma, idioma, datos, usuario_log=user_id,
                               nombre=contexto.nombre_de(user))
    eventos = _con_memoria(eventos, id_usuario, plataforma, conversacion, turno['texto'])
    if idioma != elegido:
        eventos = _con_cambio_de_idioma(eventos, idioma)
    return _respuesta_por_frases(eventos)


def responder_voz(user, data):
    """Audio de una frase de Gotita con la voz natural (ElevenLabs)."""
    if not voz.disponible():
        return _error(503, 'La voz natural no está activada.')
    if not isinstance(data, dict):
        return _error(400, 'Formato no válido.')
    texto = str(data.get('texto') or '').strip()
    if not texto:
        return _error(400, 'No hay nada que decir.')
    user_id = str(owner_id(user) or 'anonimo')
    # Límites para cuidar el saldo de ElevenLabs.
    permitida, espera = hit('asistente_voz_minuto', user_id, rate_for('asistente_voz_minuto', VOZ_POR_MINUTO))
    if permitida:
        limite_dia = rate_for('asistente_voz_dia', f'{settings.ASISTENTE_VOZ_POR_DIA}/d')
        permitida, espera = hit('asistente_voz_dia', user_id, limite_dia)
    if not permitida:
        return _error(429, 'Por ahora uso mi otra voz.', espera)
    if not proteccion.tope_voz():
        return _error(429, 'Por hoy uso mi otra voz.')
    idioma = data.get('idioma') if data.get('idioma') in ('es', 'en') else avisos.idioma_de(user)
    try:
        audio = voz.sintetizar(texto, idioma, lenta=bool(data.get('lenta')))
    except voz.VozNoDisponible as exc:
        return _error(503 if exc.sin_saldo else 502, 'No pude usar la voz natural en este momento.')
    response = HttpResponse(audio, content_type='audio/mpeg')
    response['Cache-Control'] = 'private, max-age=3600'
    return response


def responder_dictado(user, audio, tipo):
    """Pasa a texto la pregunta que la persona dijo en voz alta (en español o inglés, sin elegir)."""
    if not voz.dictado_disponible():
        return JsonResponse({'ok': False, 'sin_permiso': True, 'error': 'El dictado natural no está activado.'},
                            status=503)
    tipo = (tipo or '').split(';')[0].strip().lower()
    if tipo not in _TIPOS_AUDIO or not (MIN_AUDIO <= len(audio) <= MAX_AUDIO) or not _TIPOS_AUDIO[tipo](audio):
        return _error(400, 'El audio no es válido.')
    user_id = str(owner_id(user) or 'anonimo')
    if proteccion.en_pausa(user_id):
        return _error(429, 'Gotita está en pausa para tu cuenta por un rato.')
    permitida, espera = hit('asistente_dictado_minuto', user_id, rate_for('asistente_dictado_minuto', DICTADO_POR_MINUTO))
    if permitida:
        limite_dia = rate_for('asistente_dictado_dia', f'{settings.ASISTENTE_DICTADO_POR_DIA}/d')
        permitida, espera = hit('asistente_dictado_dia', user_id, limite_dia)
    if not permitida or not proteccion.tope_dictado():
        return JsonResponse({'ok': False, 'sin_permiso': True, 'error': 'Por ahora uso el dictado del navegador.'},
                            status=429)
    try:
        resultado = voz.dictar(audio, tipo)
    except voz.VozNoDisponible as exc:
        return JsonResponse({'ok': False, 'sin_permiso': exc.sin_permiso or exc.sin_saldo,
                             'error': 'No pude entender el audio en este momento.'}, status=503 if exc.sin_permiso else 502)
    texto = ' '.join(resultado['texto'].split())[:600]
    return JsonResponse({'ok': True, 'texto': texto, 'idioma': resultado['idioma']})


def _estado(user, plataforma):
    rol = destinos.rol_de(user)
    return {
        'ok': True,
        'activa': claude.disponible(),
        'voz_natural': voz.disponible(),
        'dictado_natural': voz.dictado_disponible(),
        'nombre': prompts.NOMBRE,
        'usuario': contexto.nombre_de(user),
        'rol': rol,
        'destinos': destinos.disponibles(rol, plataforma),
        'dias_historial': historial.DIAS,
    }


# ── Historial (igual para la web y la app) ───────────────────────────────────

def _limite_historial(user):
    permitida, espera = hit('gotita_historial', str(owner_id(user)), rate_for('gotita_historial', '60/m'))
    return None if permitida else _error(429, 'Demasiadas consultas del historial.', espera)


def lista_conversaciones(user):
    bloqueo = _limite_historial(user)
    if bloqueo is not None:
        return bloqueo
    return JsonResponse({'ok': True, 'dias': historial.DIAS, 'conversaciones': historial.lista(owner_id(user))})


def una_conversacion(user, conversacion_id, borrar=False):
    bloqueo = _limite_historial(user)
    if bloqueo is not None:
        return bloqueo
    if borrar:
        if not historial.borrar(owner_id(user), conversacion_id):
            return _error(404, 'Esa conversación ya no existe.')
        return JsonResponse({'ok': True})
    conversacion = historial.mensajes(owner_id(user), conversacion_id)
    if conversacion is None:
        return _error(404, 'Esa conversación ya no existe.')
    return JsonResponse({'ok': True, **conversacion})


def borrar_conversaciones(user):
    bloqueo = _limite_historial(user)
    if bloqueo is not None:
        return bloqueo
    historial.borrar_todo(owner_id(user))
    return JsonResponse({'ok': True})


# ── Página web ───────────────────────────────────────────────────────────────

def _demasiado_grande(request):
    try:
        return int(request.META.get('CONTENT_LENGTH') or 0) > proteccion.MAX_CUERPO
    except ValueError:
        return True


@require_POST
def turno_web(request):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión para hablar con Gotita.')
    if _demasiado_grande(request):
        return _error(413, 'La pregunta es demasiado grande.')
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return _error(400, 'Formato no válido.')
    return responder_turno(request.user, data, destinos.WEB)


@require_POST
def voz_web(request):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión.')
    if _demasiado_grande(request):
        return _error(413, 'El texto es demasiado grande.')
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return _error(400, 'Formato no válido.')
    return responder_voz(request.user, data)


@require_POST
def dictado_web(request):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión.')
    if _demasiado_grande(request):
        return _error(413, 'El audio es demasiado grande.')
    return responder_dictado(request.user, request.body, request.content_type)


@require_GET
def avisos_web(request):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión.')
    permitida, espera = hit('gotita_avisos', str(owner_id(request.user)), rate_for('gotita_avisos', '20/m'))
    if not permitida:
        return _error(429, 'Demasiadas consultas de avisos.', espera)
    lista = avisos.avisos_para(request.user)
    for aviso in lista:
        aviso['url'] = destinos.url_web(aviso.get('destino'))
    return JsonResponse({'ok': True, 'avisos': lista})


@require_http_methods(['GET', 'DELETE'])
def conversaciones_web(request):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión.')
    if request.method == 'DELETE':
        return borrar_conversaciones(request.user)
    return lista_conversaciones(request.user)


@require_http_methods(['GET', 'DELETE'])
def conversacion_web(request, conversacion_id):
    if not request.user.is_authenticated:
        return _error(401, 'Inicia sesión.')
    return una_conversacion(request.user, conversacion_id, borrar=request.method == 'DELETE')


# ── App del teléfono ─────────────────────────────────────────────────────────

class _RespuestaPorFrases(BaseRenderer):
    """Permite que la app pida la respuesta frase por frase (si hay un error, se manda como texto normal)."""
    media_type = 'text/event-stream'
    format = 'frases'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data, ensure_ascii=False).encode('utf-8')


class AsistenteEstadoView(APIView):
    """Le dice a la app si Gotita está encendida y a qué pantallas puede llevar a esta persona."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response(_estado(request.user, destinos.APP))


class AsistenteTurnoView(APIView):
    """Una pregunta a Gotita desde la app (la respuesta llega frase por frase)."""
    permission_classes = [IsAuthenticatedUser]
    renderer_classes = [JSONRenderer, _RespuestaPorFrases]

    def post(self, request):
        if _demasiado_grande(request):
            return _error(413, 'La pregunta es demasiado grande.')
        return responder_turno(request.user, request.data, destinos.APP)


class _AudioMp3(BaseRenderer):
    """Permite que la app pida el audio de la voz (si hay un error, se manda como texto normal)."""
    media_type = 'audio/mpeg'
    format = 'mp3'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data, ensure_ascii=False).encode('utf-8')


class AsistenteVozView(APIView):
    """Audio de una frase de Gotita con la voz natural, para la app."""
    permission_classes = [IsAuthenticatedUser]
    renderer_classes = [JSONRenderer, _AudioMp3]

    def post(self, request):
        return responder_voz(request.user, request.data)


class AsistenteConversacionesView(APIView):
    """Historial de conversaciones con Gotita (de los últimos 20 días) desde la app."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return lista_conversaciones(request.user)

    def delete(self, request):
        return borrar_conversaciones(request.user)


class AsistenteConversacionView(APIView):
    """Una conversación del historial: ver sus mensajes o borrarla."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, conversacion_id):
        return una_conversacion(request.user, conversacion_id)

    def delete(self, request, conversacion_id):
        return una_conversacion(request.user, conversacion_id, borrar=True)
