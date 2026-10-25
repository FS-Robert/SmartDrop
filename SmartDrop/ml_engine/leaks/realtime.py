"""Detección de fugas al instante, lectura por lectura (PB026).

El análisis de `detect.py` mira el historial por horas; este módulo revisa cada lectura apenas llega (el ESP32
manda flujo y presión cada 2 s) para avisar en segundos:

- *Posible Fuga* (flujo nocturno): entre las 3:00 y las 6:00 casi nadie usa agua. Si el flujo se mantiene por
  encima de 0.5 L/min durante 20 s seguidos, se registra la fuga. Con el "Modo de Prueba Nocturna" encendido
  (desde la página de fugas) se revisa a cualquier hora, para poder probarlo de día abriendo la llave 30 s.
- *Fuga Crítica por Presión*: la presión cae de golpe por debajo del umbral de calibración mientras el flujo
  sigue constante (el agua se va por otro lado).

Cada evento queda en Supabase (fuga + alerta + notificación a los admins), avisa a los vecinos del sector
(PB027) y se manda al instante a la web y a la app por WebSocket.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MODO_PRUEBA_KEY = 'modo_prueba_nocturna'
TIPO_NOCTURNA = 'fuga_nocturna'
TIPO_PRESION = 'fuga_critica_presion'
TITULOS = {TIPO_NOCTURNA: 'Posible Fuga', TIPO_PRESION: 'Fuga Crítica por Presión'}
ESTADO_SENSOR = {TIPO_NOCTURNA: 'Posible fuga', TIPO_PRESION: 'Fuga crítica'}
VENTANA_SEGUNDOS = 60


def _ajuste(nombre, defecto):
    return type(defecto)(getattr(settings, nombre, defecto))


def hora_inicio():
    return _ajuste('LEAK_RT_NIGHT_START', 3)


def hora_fin():
    return _ajuste('LEAK_RT_NIGHT_END', 6)


def flujo_minimo():
    return _ajuste('LEAK_RT_FLOW_MIN_LPM', 0.5)


def segundos_flujo():
    return _ajuste('LEAK_RT_FLOW_SECONDS', 20.0)


def umbral_presion():
    return _ajuste('LEAK_RT_PRESSURE_THRESHOLD_KPA', 5.0)


def caida_presion():
    return _ajuste('LEAK_RT_PRESSURE_DROP_KPA', 3.0)


def minutos_espera():
    return _ajuste('LEAK_RT_COOLDOWN_MINUTES', 10.0)


@dataclass
class _Vivienda:
    flujos: deque = field(default_factory=deque)      # (momento, L/min)
    presiones: deque = field(default_factory=deque)   # (momento, kPa)
    flujo_desde: object = None
    ultimo_aviso: dict = field(default_factory=dict)  # tipo -> momento


_estado = {}
_estados_sensor = {}  # id_sensor -> {'estado', 'tipo', 'desde', 'id_alerta'}
_lock = threading.Lock()


def reiniciar():
    """Olvida todo lo acumulado (para las pruebas)."""
    with _lock:
        _estado.clear()
        _estados_sensor.clear()


# ── Modo de Prueba Nocturna ──────────────────────────────────────────────────

def modo_prueba_activo():
    from ml_engine.models import MonitorState

    fila = MonitorState.objects.filter(key=MODO_PRUEBA_KEY).first()
    return bool(fila and (fila.last_summary or {}).get('activo'))


def cambiar_modo_prueba(activo, usuario=None):
    from ml_engine.models import MonitorState

    MonitorState.objects.update_or_create(key=MODO_PRUEBA_KEY, defaults={
        'last_started_at': timezone.now(), 'last_status': 'activo' if activo else 'apagado',
        'last_summary': {'activo': bool(activo), 'por': usuario},
    })
    logger.warning('Modo de Prueba Nocturna %s por %s', 'encendido' if activo else 'apagado', usuario)


def en_horario_nocturno(momento, modo_prueba=None):
    """True entre las 3:00 y las 6:00 (hora local) o si el Modo de Prueba Nocturna está encendido."""
    if modo_prueba is None:
        modo_prueba = modo_prueba_activo()
    if modo_prueba:
        return True
    hora = timezone.localtime(momento).hour
    return hora_inicio() <= hora < hora_fin()


# ── Estado de los sensores para el panel del admin ───────────────────────────

def estados_sensores():
    """Sensores con una fuga detectada en los últimos minutos: {id_sensor: {...}}."""
    limite = timezone.now() - timedelta(minutes=max(minutos_espera(), 30))
    with _lock:
        for id_sensor in [k for k, v in _estados_sensor.items() if v['desde'] < limite]:
            _estados_sensor.pop(id_sensor, None)
        return {k: dict(v) for k, v in _estados_sensor.items()}


# ── Revisión de cada lectura ─────────────────────────────────────────────────

def _recortar(cola, momento):
    while cola and (momento - cola[0][0]).total_seconds() > VENTANA_SEGUNDOS:
        cola.popleft()


def _en_espera(estado, tipo, momento):
    anterior = estado.ultimo_aviso.get(tipo)
    return anterior is not None and momento - anterior < timedelta(minutes=minutos_espera())


def _flujo_constante(estado, momento):
    """El flujo de los últimos 10 s existe, no es cero y casi no cambia (±30 %)."""
    recientes = [valor for cuando, valor in estado.flujos if (momento - cuando).total_seconds() <= 10]
    if len(recientes) < 2 or min(recientes) < flujo_minimo():
        return False
    promedio = sum(recientes) / len(recientes)
    return max(recientes) - min(recientes) <= max(0.3 * promedio, 0.2)


def revisar_lectura(id_vivienda, tipo, valor, id_sensor=None, momento=None, alerta_esp32=False):
    """Revisa una lectura de flujo o presión. Si encuentra una fuga la registra y devuelve el evento."""
    if tipo not in ('flujo', 'presion') or valor is None:
        return None
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return None
    momento = momento or timezone.now()
    detectado = None
    with _lock:
        estado = _estado.setdefault(id_vivienda, _Vivienda())
        if tipo == 'flujo':
            estado.flujos.append((momento, valor))
            _recortar(estado.flujos, momento)
            if valor >= flujo_minimo():
                estado.flujo_desde = estado.flujo_desde or momento
            else:
                estado.flujo_desde = None
        else:
            estado.presiones.append((momento, valor))
            _recortar(estado.presiones, momento)
        datos = {'flujo_desde': estado.flujo_desde, 'flujo_constante': _flujo_constante(estado, momento),
                 'presiones': list(estado.presiones), 'flujos': list(estado.flujos)}
        espera_nocturna = _en_espera(estado, TIPO_NOCTURNA, momento)
        espera_presion = _en_espera(estado, TIPO_PRESION, momento)

    if tipo == 'flujo' and datos['flujo_desde'] is not None and not espera_nocturna:
        segundos = (momento - datos['flujo_desde']).total_seconds()
        if segundos >= segundos_flujo():
            modo_prueba = modo_prueba_activo()
            if en_horario_nocturno(momento, modo_prueba):
                detectado = {
                    'tipo': TIPO_NOCTURNA, 'id_sensor_flujo': id_sensor, 'modo_prueba': modo_prueba,
                    'metricas': {'flujo_lpm': round(valor, 3), 'segundos_con_flujo': round(segundos, 1),
                                 'flujo_minimo_lpm': flujo_minimo()},
                }
    elif tipo == 'presion' and not espera_presion:
        anteriores = [p for cuando, p in datos['presiones'] if 0 < (momento - cuando).total_seconds() <= 12]
        referencia = max(anteriores) if anteriores else None
        caida = (referencia - valor) if referencia is not None else 0.0
        repentina = caida >= caida_presion() or bool(alerta_esp32)
        if valor < umbral_presion() and repentina and datos['flujo_constante']:
            flujo = [v for _, v in datos['flujos'][-5:]]
            detectado = {
                'tipo': TIPO_PRESION, 'id_sensor_presion': id_sensor, 'modo_prueba': False,
                'metricas': {'presion_kpa': round(valor, 3),
                             'presion_anterior_kpa': None if referencia is None else round(referencia, 3),
                             'caida_presion': round(caida, 3), 'umbral_kpa': umbral_presion(),
                             'flujo_lpm': round(sum(flujo) / len(flujo), 3) if flujo else None},
            }

    if detectado is None:
        return None
    with _lock:
        _estado[id_vivienda].ultimo_aviso[detectado['tipo']] = momento
        if detectado['tipo'] == TIPO_NOCTURNA:
            _estado[id_vivienda].flujo_desde = None
    detectado.update({'id_vivienda': id_vivienda, 'momento': momento, 'titulo': TITULOS[detectado['tipo']]})
    try:
        registrar_evento(detectado)
    except Exception:
        logger.exception('No se pudo registrar la fuga detectada en la vivienda %s', id_vivienda)
    return detectado


# ── Registro y avisos ────────────────────────────────────────────────────────

def _vivienda(id_vivienda):
    from App import supabase_client

    if not id_vivienda:
        return {}
    filas = supabase_client.select('vivienda', 'id_vivienda,nic,direccion,zona,nombre_completo_titular', {
        'id_vivienda': f'eq.{int(id_vivienda)}', 'limit': '1',
    })
    return filas[0] if filas else {}


def _mensaje(evento, vivienda):
    lugar = ', '.join(p for p in (vivienda.get('direccion'), vivienda.get('zona')) if p)
    cabeza = f"{evento['titulo']} en {vivienda.get('nic') or 'una vivienda'}" + (f' — {lugar}.' if lugar else '.')
    m = evento['metricas']
    if evento['tipo'] == TIPO_NOCTURNA:
        detalle = (f"El agua corrió {m['segundos_con_flujo']:.0f} s seguidos ({m['flujo_lpm']:.2f} L/min) en horario "
                   'nocturno, cuando no debería haber consumo.')
        if evento.get('modo_prueba'):
            detalle += ' (Modo de Prueba Nocturna)'
    else:
        antes = m.get('presion_anterior_kpa')
        detalle = (f"La presión bajó de golpe a {m['presion_kpa']:.2f} kPa"
                   + (f' (antes {antes:.2f} kPa)' if antes is not None else '')
                   + ' mientras el agua seguía entrando: puede haber una ruptura en la tubería.')
    return f'{cabeza}\n{detalle}\nQué hacer: revisar la tubería y, si el agua sigue corriendo, cerrar la válvula.'


def registrar_evento(evento):
    """Guarda fuga + alerta + notificaciones a los admins, avisa al sector y lo manda en vivo."""
    from App import supabase_client
    from ml_engine.leaks.alerts import ORIGIN
    from ml_engine.leaks.sector import avisar_admins_en_vivo, avisar_sector
    from ml_engine.supabase_out import admin_user_ids

    vivienda = _vivienda(evento.get('id_vivienda'))
    momento = evento['momento']
    fecha = momento.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    m = evento['metricas']
    critica = evento['tipo'] == TIPO_PRESION
    fuga = supabase_client.insert('fuga', {
        'fecha_deteccion': fecha,
        'ubicacion_estimada': vivienda.get('direccion'),
        'zona': vivienda.get('zona'),
        'id_sensor_presion': evento.get('id_sensor_presion'),
        'id_sensor_flujo': evento.get('id_sensor_flujo'),
        'caida_presion': m.get('caida_presion'),
        'flujo_anomalo': m.get('flujo_lpm'),
        'tipo_fuga': 'posible_fuga',
        'metodo_deteccion': 'caida_presion' if critica else 'flujo_nocturno',
        'estado': 'pendiente',
    }) or {}
    mensaje = _mensaje(evento, vivienda)
    alerta = supabase_client.insert('alerta', {
        'tipo_alerta': 'fuga',
        'prioridad': 'critica' if critica else 'alta',
        'mensaje': mensaje,
        'estado_confirmacion': 'pendiente',
        'id_fuga': fuga.get('id_fuga'),
        'fecha_creacion': momento.isoformat(),
        'datos_adicionales': {
            'origen': ORIGIN,
            'tipo': evento['tipo'],
            'titulo': evento['titulo'],
            'metodo': 'tiempo_real',
            'modo_prueba': bool(evento.get('modo_prueba')),
            'vivienda': {
                'id_vivienda': vivienda.get('id_vivienda') or evento.get('id_vivienda'),
                'nic': vivienda.get('nic'), 'direccion': vivienda.get('direccion'), 'zona': vivienda.get('zona'),
                'titular': vivienda.get('nombre_completo_titular'),
            },
            'sensores': {'flujo': evento.get('id_sensor_flujo'), 'presion': evento.get('id_sensor_presion')},
            'metricas': m,
        },
    }) or {}
    ahora = timezone.now().isoformat()
    for user_id in admin_user_ids():
        supabase_client.insert('notificacion', {
            'id_alerta': alerta.get('id_alerta'), 'id_usario_destino': user_id, 'canal_envio': 'dashboard',
            'estado_visualizacion': 'no_leida', 'fecha_envio': ahora,
        })
    evento.update({'id_fuga': fuga.get('id_fuga'), 'id_alerta': alerta.get('id_alerta'), 'mensaje': mensaje})

    id_sensor = evento.get('id_sensor_presion') or evento.get('id_sensor_flujo')
    if id_sensor:
        with _lock:
            _estados_sensor[id_sensor] = {'estado': ESTADO_SENSOR[evento['tipo']], 'tipo': evento['tipo'],
                                          'desde': momento, 'id_alerta': alerta.get('id_alerta')}
    avisar_admins_en_vivo({
        'id_alerta': alerta.get('id_alerta'), 'tipo': evento['tipo'], 'titulo': evento['titulo'],
        'prioridad': 'critica' if critica else 'alta', 'mensaje': mensaje, 'nic': vivienda.get('nic'),
        'direccion': vivienda.get('direccion'), 'zona': vivienda.get('zona'),
        'id_sensor': id_sensor, 'estado_sensor': ESTADO_SENSOR[evento['tipo']],
    })
    avisar_sector(vivienda.get('zona'), alerta.get('id_alerta'), evento['titulo'], momento)
    logger.warning('%s: vivienda=%s alerta=%s', evento['titulo'], evento.get('id_vivienda'), alerta.get('id_alerta'))
    return evento
