"""Lógica única de la electroválvula: la usan la web (views.valvula_comando) y la app (api/views_valves).

Antes cada lado tenía su propia versión y la app no registraba nada en `log_valvula` ni podía usar el
temporizador. Aquí vive todo lo que cambia el estado físico de la válvula:

* `ejecutar_comando`: publica por MQTT, actualiza `valvula` y deja el movimiento en `log_valvula`.
* Temporizador: un `threading.Timer` por válvula para cerrar a tiempo, más un vigilante
  (`vigilar_temporizadores`) que, a partir de `log_valvula`, vuelve a programar o ejecuta el cierre
  si el servidor se reinició con una apertura temporizada pendiente.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from django.utils import timezone

from . import supabase_client
from .mqtt_service import MqttError, publish_command as _mqtt_publish

logger = logging.getLogger(__name__)

TIMER_MIN_SECONDS = 5
TIMER_MAX_SECONDS = 120
COMANDOS = ('abrir', 'cerrar')
TIPO_TEMPORIZADO = 'temporizado'
ESTADOS_ABIERTA = {'abierta', 'abierto', 'open'}

_VALVE_COLUMNS = 'id_valvula,nombre,estado_actual,topic_mqtt_comando,ultima_apertura'

_REGISTRY = {}
_LOCK = threading.Lock()


class ValvulaNoEncontrada(MqttError):
    """La válvula no existe o no tiene topic MQTT. Hereda de MqttError para que la web la trate igual que antes."""


# ── Validación ───────────────────────────────────────────────────────────────

def parse_duracion(raw_value):
    """Segundos del temporizador (5 a 120) o None si no se pidió temporizador ('', None o 0).

    Lanza ValueError con un mensaje para el usuario si el valor no es válido.
    """
    if isinstance(raw_value, bool):
        raise ValueError('La duración programada debe ser un número entero válido.')
    if raw_value is None:
        return None
    texto = str(raw_value).strip()
    if texto == '':
        return None
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        raise ValueError('La duración programada debe ser un número entero válido.') from None
    if numero != numero or numero in (float('inf'), float('-inf')):
        raise ValueError('La duración programada debe ser un número entero válido.')
    duration = int(numero)
    if duration == 0:
        return None
    if duration < TIMER_MIN_SECONDS:
        raise ValueError('El tiempo mínimo permitido es de 5 segundos')
    if duration > TIMER_MAX_SECONDS:
        raise ValueError('Tiempo máximo permitido: 120 seg (2 min).')
    return duration


def normalizar_comando(raw):
    """'ABRIR', 'abrir', 'abrir-remoto' → 'abrir'. Lanza ValueError si no es abrir/cerrar."""
    comando = str(raw or '').strip().lower().replace('-remoto', '')
    if comando not in COMANDOS:
        raise ValueError('Comando inválido. Usa ABRIR o CERRAR.')
    return comando


def parse_timestamp(value):
    """ISO 8601 de Supabase a datetime con zona; None si no se puede leer."""
    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        try:
            timestamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


def esta_abierta(estado):
    return str(estado or '').strip().lower() in ESTADOS_ABIERTA


# ── Registro en log_valvula ──────────────────────────────────────────────────

def insertar_log(payload):
    """Inserta el movimiento en `log_valvula`.

    Si Supabase rechaza el origen (por ejemplo, una restricción CHECK que solo conoce 'web'), se reintenta
    una vez con origen 'web' y el origen real anotado en `razon`, para no perder el registro.
    """
    try:
        return supabase_client.insert('log_valvula', payload)
    except supabase_client.SupabaseError as exc:
        origen = payload.get('origen_accion')
        if exc.status_code != 400 or origen in (None, 'web'):
            raise
        logger.warning('log_valvula rechazó origen_accion=%r (%s); se registra como web.', origen, exc)
        respaldo = dict(payload, origen_accion='web')
        respaldo['razon'] = ' · '.join(filter(None, [payload.get('razon'), f'Origen: {origen}']))
        return supabase_client.insert('log_valvula', respaldo)


# ── Temporizador en memoria ──────────────────────────────────────────────────

def timer_info(valvula_id):
    """Estado del temporizador con las claves que usa la plantilla web."""
    entry = _REGISTRY.get(str(valvula_id))
    if not entry:
        return {'timer_activo': False, 'timer_remaining': 0, 'timer_end_at': None, 'duracion_seg': 0}
    expires_at = entry.get('expires_at')
    remaining = max(0, int((expires_at - timezone.now()).total_seconds())) if expires_at else 0
    return {
        'timer_activo': True,
        'timer_remaining': remaining,
        'timer_end_at': expires_at.isoformat() if expires_at else None,
        'duracion_seg': entry.get('duracion_seg', 0),
    }


def temporizador_para_app(valvula_id):
    """Objeto `temporizador_activo` que lee la app Android, o None si no hay temporizador."""
    info = timer_info(valvula_id)
    if not info['timer_activo']:
        return None
    return {
        'duracion_programada': info['duracion_seg'],
        'segundos_restantes': info['timer_remaining'],
        'fin': info['timer_end_at'],
    }


def cancel_timer(valvula_id):
    with _LOCK:
        entry = _REGISTRY.pop(str(valvula_id), None)
    if entry is not None:
        timer = entry.get('timer')
        if timer is not None and timer.is_alive():
            timer.cancel()
    return entry


def start_timer(valvula_id, duracion_seg, expires_at=None, publish=None):
    """Programa el cierre automático. Con `expires_at` (recuperación) espera solo lo que falta."""
    cancel_timer(valvula_id)
    expires_at = expires_at or timezone.now() + timedelta(seconds=duracion_seg)
    delay = max(0.0, (expires_at - timezone.now()).total_seconds())
    timer = threading.Timer(
        delay, cierre_automatico, args=(valvula_id, duracion_seg),
        kwargs={'publish': publish},
    )
    timer.daemon = True
    with _LOCK:
        _REGISTRY[str(valvula_id)] = {'timer': timer, 'duracion_seg': duracion_seg, 'expires_at': expires_at}
    timer.start()
    return timer_info(valvula_id)


def cierre_automatico(valvula_id, duracion_seg, razon='Cierre automático - Tiempo completado', publish=None):
    """Cierra la válvula al terminar el temporizador y lo deja registrado en `log_valvula`."""
    publish = publish or _mqtt_publish
    try:
        rows = supabase_client.select('valvula', _VALVE_COLUMNS, {'id_valvula': f'eq.{int(valvula_id)}', 'limit': '1'})
        valvula = rows[0] if rows else None
        if not valvula:
            logger.warning('No se encontró la válvula %s para el cierre automático.', valvula_id)
            return False
        topic = valvula.get('topic_mqtt_comando')
        if topic:
            publish(topic, 'cerrar')
        supabase_client.update(
            'valvula', {'estado_actual': 'cerrada', 'ultima_apertura': None},
            {'id_valvula': f'eq.{int(valvula_id)}'},
        )
        insertar_log({
            'id_valvula': valvula['id_valvula'],
            'accion': 'cerrar',
            'estado_anterior': valvula.get('estado_actual') or 'desconocida',
            'estado_nuevo': 'cerrada',
            'tipo_activacion': TIPO_TEMPORIZADO,
            'id_usuario': None,
            'fecha_hora': timezone.now().isoformat(),
            'ip_dispositivo': 'temporizador',
            'origen_accion': 'servidor',
            'duracion_programada': duracion_seg,
            'duracion_real': _segundos_abierta(valvula) or duracion_seg,
            'razon': razon,
        })
        return True
    except Exception:
        # Si falla (MQTT o Supabase caídos), el vigilante lo reintenta en su siguiente vuelta.
        logger.exception('No se pudo ejecutar el cierre automático de la válvula %s.', valvula_id)
        return False
    finally:
        # Solo se quita el registro si es el de este mismo temporizador: si mientras tanto alguien
        # volvió a abrir con otro tiempo, ese temporizador nuevo se conserva.
        with _LOCK:
            entry = _REGISTRY.get(str(valvula_id))
            if entry is not None and entry.get('timer') is threading.current_thread():
                _REGISTRY.pop(str(valvula_id), None)


def _segundos_abierta(valvula, now=None):
    abierta_desde = parse_timestamp(valvula.get('ultima_apertura'))
    if not abierta_desde or not esta_abierta(valvula.get('estado_actual')):
        return None
    return max(0, int(((now or timezone.now()) - abierta_desde).total_seconds()))


# ── Comando (web y app) ──────────────────────────────────────────────────────

def ejecutar_comando(valvula_id, comando, *, duracion_seg=None, id_usuario=None, ip=None, origen='web', publish=None):
    """Abre o cierra la válvula, la deja registrada en `log_valvula` y devuelve el resultado.

    `publish` permite que cada vista pase su propia referencia de `publish_command` (las pruebas la
    reemplazan). Lanza ValvulaNoEncontrada, MqttError o supabase_client.SupabaseError.
    """
    publish = publish or _mqtt_publish
    comando = normalizar_comando(comando)
    if comando == 'cerrar':
        duracion_seg = None
    valvula_id = int(valvula_id)

    rows = supabase_client.select('valvula', _VALVE_COLUMNS, {'id_valvula': f'eq.{valvula_id}', 'limit': '1'})
    valvula = rows[0] if rows else None
    if not valvula:
        raise ValvulaNoEncontrada('La electroválvula no existe.')
    if not valvula.get('topic_mqtt_comando'):
        raise ValvulaNoEncontrada('La electroválvula no tiene un topic MQTT configurado.')

    timer_ya_activo = timer_info(valvula_id)['timer_activo']
    publish(valvula['topic_mqtt_comando'], comando)

    now = timezone.now()
    estado_anterior = valvula.get('estado_actual') or 'desconocida'
    estado_nuevo = 'abierta' if comando == 'abrir' else 'cerrada'
    segundos_abierta = _segundos_abierta(valvula, now)

    if comando == 'abrir':
        # Si ya estaba abierta se conserva la hora original de apertura.
        abierta_desde = valvula.get('ultima_apertura') if esta_abierta(estado_anterior) and valvula.get('ultima_apertura') else now.isoformat()
        update_payload = {'estado_actual': estado_nuevo, 'ultima_apertura': abierta_desde}
    else:
        update_payload = {'estado_actual': estado_nuevo, 'ultima_apertura': None}
    supabase_client.update('valvula', update_payload, {'id_valvula': f'eq.{valvula_id}'})

    # Cerrar, o abrir sin tiempo, cancela un temporizador pendiente; abrir con tiempo lo reinicia.
    if duracion_seg:
        info = start_timer(valvula_id, duracion_seg, publish=publish)
    else:
        if timer_ya_activo:
            cancel_timer(valvula_id)
        info = timer_info(valvula_id)

    razon = None
    if comando == 'cerrar' and timer_ya_activo:
        razon = 'Cierre manual durante temporizador'
    elif comando == 'abrir' and duracion_seg:
        razon = f'Apertura temporizada de {duracion_seg} segundos'
    elif comando == 'abrir' and timer_ya_activo:
        razon = 'Apertura manual: se canceló el temporizador'

    log_payload = {
        'id_valvula': valvula['id_valvula'],
        'accion': comando,
        'estado_anterior': estado_anterior,
        'estado_nuevo': estado_nuevo,
        'tipo_activacion': TIPO_TEMPORIZADO if duracion_seg else 'manual',
        'id_usuario': id_usuario,
        'fecha_hora': now.isoformat(),
        'ip_dispositivo': ip,
        'origen_accion': origen,
        'duracion_programada': duracion_seg,
        'duracion_real': duracion_seg if duracion_seg else (segundos_abierta if comando == 'cerrar' else None),
        'razon': razon,
    }
    log_row = None
    try:
        log_row = insertar_log(log_payload)
    except supabase_client.SupabaseError:
        # La válvula ya se movió: no se revierte, pero queda constancia en el log del servidor.
        logger.exception('La válvula %s cambió a %s pero no se pudo registrar en log_valvula.', valvula_id, estado_nuevo)
    registrado = isinstance(log_row, dict) and bool(log_row)
    log_row = log_row if registrado else log_payload

    return {
        'valvula': valvula,
        'id_valvula': valvula['id_valvula'],
        'accion': comando,
        'estado_anterior': estado_anterior,
        'estado_nuevo': estado_nuevo,
        'tipo_activacion': log_payload['tipo_activacion'],
        'fecha_hora': log_row.get('fecha_hora', log_payload['fecha_hora']),
        'ultima_apertura': update_payload['ultima_apertura'],
        'duracion_programada': duracion_seg,
        'log_registrado': registrado,
        **info,
    }


# ── Recuperación tras un reinicio ────────────────────────────────────────────

def _ultimo_movimiento(valvula_id):
    rows = supabase_client.select(
        'log_valvula',
        'id_valvula,accion,tipo_activacion,fecha_hora,duracion_programada',
        {'id_valvula': f'eq.{int(valvula_id)}', 'order': 'fecha_hora.desc', 'limit': '1'},
    )
    return rows[0] if rows else None


def recuperar_temporizadores(publish=None, now=None):
    """Revisa las válvulas abiertas con una apertura temporizada pendiente.

    Si el tiempo ya pasó (el servidor estaba caído o el cierre falló) la cierra ahora; si no, vuelve
    a programar el cierre para el tiempo que falta. Devuelve {'cerradas': [...], 'reprogramadas': [...]}.
    """
    now = now or timezone.now()
    resultado = {'cerradas': [], 'reprogramadas': []}
    valvulas = supabase_client.select('valvula', 'id_valvula,estado_actual', {'order': 'id_valvula.asc', 'limit': '100'})
    for valvula in valvulas:
        valvula_id = valvula.get('id_valvula')
        if valvula_id is None or not esta_abierta(valvula.get('estado_actual')) or str(valvula_id) in _REGISTRY:
            continue
        movimiento = _ultimo_movimiento(valvula_id)
        if (
            not movimiento
            or movimiento.get('accion') != 'abrir'
            or str(movimiento.get('tipo_activacion') or '').lower() != TIPO_TEMPORIZADO
        ):
            continue
        try:
            duracion = int(movimiento.get('duracion_programada') or 0)
        except (TypeError, ValueError):
            continue
        inicio = parse_timestamp(movimiento.get('fecha_hora'))
        if not duracion or not inicio:
            continue
        fin = inicio + timedelta(seconds=duracion)
        if fin <= now:
            cierre_automatico(
                valvula_id, duracion,
                razon='Cierre automático - el tiempo terminó mientras el servidor no estaba disponible',
                publish=publish,
            )
            resultado['cerradas'].append(valvula_id)
        else:
            start_timer(valvula_id, duracion, expires_at=fin, publish=publish)
            resultado['reprogramadas'].append(valvula_id)
    return resultado


def vigilar_temporizadores(intervalo, espera_inicial):
    """Hilo del servidor: recupera los temporizadores al arrancar y luego revisa cada `intervalo` segundos."""
    time.sleep(espera_inicial)
    while True:
        try:
            resultado = recuperar_temporizadores()
            if resultado['cerradas'] or resultado['reprogramadas']:
                logger.info('Temporizadores de válvula recuperados: %s', resultado)
        except Exception:  # noqa: BLE001 — sin conexión momentánea: se reintenta en la siguiente vuelta.
            logger.warning('No se pudieron revisar los temporizadores de las válvulas', exc_info=True)
        time.sleep(intervalo)
