"""Voz natural de Gotita con ElevenLabs: el servidor convierte sus frases en audio y, al revés, pasa a texto
lo que dice la persona (dictado), detectando solo si habla español o inglés.

La llave de ElevenLabs solo vive en el .env del servidor (nunca llega a la web ni a la app). Si no hay llave,
se acabó el saldo o ElevenLabs falla, la web y la app usan la voz del navegador o del teléfono.
Para no gastar saldo: las respuestas se guardan un día en memoria, y las frases cortas que se repiten
(saludos, "Listo, te explico en modo técnico", avisos de error...) se guardan 30 días en una carpeta del
servidor, así se generan una sola vez aunque el servidor se reinicie.
"""
import hashlib
import logging
import os
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache

from .seguridad import filtrar_texto

logger = logging.getLogger(__name__)

URL = 'https://api.elevenlabs.io/v1/text-to-speech/{voz}'
URL_DICTADO = 'https://api.elevenlabs.io/v1/speech-to-text'
SIN_PERMISO_DICTADO = 'gotita:dictado:sin_permiso'
FORMATO = 'mp3_44100_128'
MAX_TEXTO = 400
GUARDAR_SEGUNDOS = 24 * 3600
MAX_GUARDADO = 400_000  # solo se guardan audios cortos
ESPERA_MAXIMA = 20
FRASE_CORTA = 160            # las frases de este largo o menos se guardan en la carpeta
DIAS_EN_CARPETA = 30
# Estos modelos entienden el idioma que se les pide (así no leen el español con acento de otro idioma).
MODELOS_CON_IDIOMA = ('eleven_flash_v2_5', 'eleven_turbo_v2_5')


class VozNoDisponible(Exception):
    """ElevenLabs no pudo hacer el audio; quien llama usa la voz del navegador o del teléfono."""

    def __init__(self, mensaje, sin_saldo=False, sin_permiso=False):
        super().__init__(mensaje)
        self.sin_saldo = sin_saldo
        self.sin_permiso = sin_permiso


def _llave():
    return os.environ.get('ELEVENLABS_API_KEY', '').strip()


def disponible():
    return bool(getattr(settings, 'ASISTENTE_ACTIVO', False)) and bool(_llave())


def _carpeta():
    return Path(getattr(settings, 'GOTITA_AUDIO_DIR', settings.BASE_DIR / 'gotita_audio'))


def _de_la_carpeta(nombre):
    archivo = _carpeta() / f'{nombre}.mp3'
    try:
        if time.time() - archivo.stat().st_mtime > DIAS_EN_CARPETA * 86400:
            return None
        return archivo.read_bytes() or None
    except OSError:
        return None


def _limpiar_carpeta():
    # Una vez al día se borran los audios que llevan más de 30 días sin hacerse de nuevo.
    if not cache.add('gotita:voz:limpieza', True, 86400):
        return
    limite = time.time() - DIAS_EN_CARPETA * 86400
    for archivo in _carpeta().glob('*.mp3'):
        try:
            if archivo.stat().st_mtime < limite:
                archivo.unlink()
        except OSError:
            pass


def _a_la_carpeta(nombre, audio):
    try:
        carpeta = _carpeta()
        carpeta.mkdir(parents=True, exist_ok=True)
        temporal = carpeta / f'{nombre}.tmp'
        temporal.write_bytes(audio)
        temporal.replace(carpeta / f'{nombre}.mp3')
        _limpiar_carpeta()
    except OSError:
        logger.warning('Gotita: no se pudo guardar un audio en la carpeta', exc_info=True)


def sintetizar(texto, idioma='es', lenta=False):
    """Audio MP3 (bytes) de la frase con la voz de Gotita."""
    idioma = idioma if idioma in ('es', 'en') else 'es'
    limpio = filtrar_texto(texto or '', idioma)[:MAX_TEXTO].strip()
    if not limpio:
        raise VozNoDisponible('Frase vacía.')
    if not disponible():
        raise VozNoDisponible('La voz natural está apagada.')

    voz = settings.ELEVENLABS_VOZ
    modelo = settings.ELEVENLABS_MODELO
    nombre = hashlib.sha256(f'{voz}|{modelo}|{idioma}|{lenta}|{limpio}'.encode('utf-8')).hexdigest()
    clave = 'gotita:voz:' + nombre
    corta = len(limpio) <= FRASE_CORTA
    guardado = cache.get(clave) or (_de_la_carpeta(nombre) if corta else None)
    if guardado:
        return guardado

    cuerpo = {
        'text': limpio,
        'model_id': modelo,
        # Estable y clara, con un poco de calidez; "lenta" baja la velocidad sin cambiar la voz.
        'voice_settings': {
            'stability': 0.55,
            'similarity_boost': 0.75,
            'style': 0.15,
            'use_speaker_boost': True,
            'speed': 0.88 if lenta else 1.0,
        },
    }
    if modelo in MODELOS_CON_IDIOMA:
        cuerpo['language_code'] = idioma
    try:
        respuesta = requests.post(
            URL.format(voz=voz),
            params={'output_format': FORMATO},
            headers={'xi-api-key': _llave(), 'Accept': 'audio/mpeg'},
            json=cuerpo,
            timeout=ESPERA_MAXIMA,
        )
    except requests.RequestException as exc:
        logger.warning('Gotita: no hubo conexión con ElevenLabs (%s)', exc.__class__.__name__)
        raise VozNoDisponible('Sin conexión con ElevenLabs.') from exc

    if respuesta.status_code != 200 or not respuesta.content:
        detalle = respuesta.text[:200] if respuesta.headers.get('Content-Type', '').startswith('application/json') else ''
        sin_saldo = respuesta.status_code in (401, 402, 429) and 'quota' in detalle.lower()
        logger.warning('Gotita: ElevenLabs respondió %s %s', respuesta.status_code, detalle)
        raise VozNoDisponible(f'ElevenLabs respondió {respuesta.status_code}.', sin_saldo=sin_saldo)

    audio = respuesta.content
    if len(audio) <= MAX_GUARDADO:
        cache.set(clave, audio, GUARDAR_SEGUNDOS)
        if corta:
            _a_la_carpeta(nombre, audio)
    return audio


# ── Dictado: de la voz de la persona a texto ──

def dictado_disponible():
    """Hay llave y no se sabe que le falte el permiso de dictado (si le falta, se vuelve a probar en 10 minutos)."""
    return disponible() and not cache.get(SIN_PERMISO_DICTADO)


def _idioma_corto(codigo):
    codigo = str(codigo or '').lower()
    if codigo in ('es', 'spa') or codigo.startswith('es-'):
        return 'es'
    if codigo in ('en', 'eng') or codigo.startswith('en-'):
        return 'en'
    return None


def dictar(audio, tipo):
    """Texto de lo que dijo la persona: {'texto', 'idioma' ('es', 'en' o None), 'seguridad' (0 a 1)}."""
    if not dictado_disponible():
        raise VozNoDisponible('El dictado natural no está disponible.', sin_permiso=True)
    try:
        respuesta = requests.post(
            URL_DICTADO,
            headers={'xi-api-key': _llave()},
            data={'model_id': settings.ELEVENLABS_DICTADO_MODELO, 'tag_audio_events': 'false'},
            files={'file': ('pregunta', audio, tipo)},
            timeout=ESPERA_MAXIMA,
        )
    except requests.RequestException as exc:
        logger.warning('Gotita: no hubo conexión con ElevenLabs para dictar (%s)', exc.__class__.__name__)
        raise VozNoDisponible('Sin conexión con ElevenLabs.') from exc

    if respuesta.status_code != 200:
        detalle = respuesta.text[:200] if respuesta.headers.get('Content-Type', '').startswith('application/json') else ''
        sin_permiso = respuesta.status_code in (401, 403) and 'permission' in detalle.lower()
        if sin_permiso:
            cache.set(SIN_PERMISO_DICTADO, True, 600)
            logger.warning('Gotita: la llave de ElevenLabs no tiene el permiso "Speech to Text"')
        else:
            logger.warning('Gotita: ElevenLabs (dictado) respondió %s %s', respuesta.status_code, detalle)
        sin_saldo = respuesta.status_code in (401, 402, 429) and 'quota' in detalle.lower()
        raise VozNoDisponible(f'ElevenLabs respondió {respuesta.status_code}.', sin_saldo=sin_saldo,
                              sin_permiso=sin_permiso)
    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise VozNoDisponible('Respuesta de dictado no válida.') from exc
    return {
        'texto': str(datos.get('text') or '').strip(),
        'idioma': _idioma_corto(datos.get('language_code')),
        'seguridad': float(datos.get('language_probability') or 0),
    }
