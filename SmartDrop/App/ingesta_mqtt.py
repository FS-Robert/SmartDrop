"""Ingesta de lecturas MQTT dentro del servidor (DEF-17, PB064).

Antes las lecturas del ESP32 solo llegaban a Supabase si `consumer.js` estaba corriendo en una laptop. Ahora el
mismo servidor de Django se suscribe a HiveMQ (TLS en el puerto 8883, con el certificado del broker verificado)
y hace lo que hacía `consumer.js`: guarda cada lectura, el historial del tanque, los incidentes de calidad y el
estado de la válvula. Además revisa fugas al instante (PB026) y manda cada lectura en vivo a la web y a la app.

Se enciende con MQTT_INGESTA_ENABLED=1 solo en el servidor. En tu computadora déjalo en 0: si no, las lecturas
se guardarían dos veces (una por cada equipo suscrito).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import supabase_client

logger = logging.getLogger(__name__)

TOPICO = 'smartdrop/#'
CALIDADES_CRITICAS = ('Pobre', 'Inaceptable')
_hilo = None


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero and abs(numero) != float('inf') else None


def _tanque_de_sensor(id_sensor):
    """Tanque al que pertenece un sensor de nivel (antes estaba fijo en consumer.js: sensor 2 -> tanque 1)."""
    filas = supabase_client.select('tanque', 'id_tanque', {'id_sensor_nivel': f'eq.{int(id_sensor)}', 'limit': '1'})
    return filas[0]['id_tanque'] if filas else None


def _guardar_lectura(id_sensor, id_vivienda, valor):
    from .views import _broadcast_sensor_reading

    datos = {'id_sensor': int(id_sensor), 'valor': valor}
    if id_vivienda:
        datos['id_vivienda'] = id_vivienda
    fila = supabase_client.insert('lectura', datos) or datos
    _broadcast_sensor_reading({**datos, **fila})


def _incidente_calidad(id_sensor, datos):
    pendientes = supabase_client.select('incidente_calidad', 'id_incidente', {
        'id_sensor_tds': f'eq.{int(id_sensor)}', 'estado': 'eq.pendiente', 'limit': '1',
    })
    if pendientes:
        return
    incidente = supabase_client.insert('incidente_calidad', {
        'fecha_deteccion': timezone.now().isoformat(), 'id_sensor_tds': int(id_sensor), 'valor_tds': datos['valor'],
        'tipo_contaminante': None, 'accion_tomada': 'ninguna', 'estado': 'pendiente',
    }) or {}
    if incidente.get('id_incidente'):
        supabase_client.insert('alerta', {
            'tipo_alerta': 'calidad_agua', 'prioridad': 'alta',
            'mensaje': f"Calidad del agua degradada ({datos.get('calidad')}): {datos['valor']} ppm",
            'estado_confirmacion': 'no_confirmada', 'id_incidente_calidad': incidente['id_incidente'],
        })


def _estado_valvula(datos):
    id_valvula, estado = datos.get('id_valvula'), datos.get('estado')
    if not id_valvula or estado not in ('abierta', 'cerrada'):
        logger.warning('Estado de válvula incompleto: %s', datos)
        return
    filas = supabase_client.select('valvula', 'estado_actual', {'id_valvula': f'eq.{int(id_valvula)}', 'limit': '1'})
    if not filas:
        logger.warning('No existe la válvula %s', id_valvula)
        return
    anterior = filas[0].get('estado_actual')
    ahora = timezone.now().isoformat()
    cambios = {'estado_actual': estado, 'ultima_conexion_mqtt': ahora}
    if estado == 'abierta':
        cambios['ultima_apertura'] = ahora
    supabase_client.update('valvula', cambios, {'id_valvula': f'eq.{int(id_valvula)}'})
    if anterior != estado:
        supabase_client.insert('log_valvula', {
            'id_valvula': int(id_valvula), 'accion': 'abrir' if estado == 'abierta' else 'cerrar',
            'estado_anterior': anterior, 'estado_nuevo': estado, 'tipo_activacion': 'remoto',
            'fecha_hora': ahora, 'origen_accion': 'esp32',
        })


def procesar_mensaje(topico, cuerpo):
    """Procesa un mensaje de `smartdrop/{id_vivienda}/{tipo}` igual que lo hacía consumer.js."""
    from ml_engine.leaks.realtime import revisar_lectura

    partes = topico.split('/')
    if len(partes) < 3 or partes[0] != 'smartdrop':
        return
    try:
        datos = json.loads(cuerpo)
    except (ValueError, UnicodeDecodeError):
        logger.warning('Mensaje MQTT que no es JSON en %s', topico)
        return
    if not isinstance(datos, dict):
        return
    if partes[2] == 'valvula':
        if len(partes) > 3 and partes[3] == 'estado':
            _estado_valvula(datos)
        return  # Los comandos a la válvula los publica el propio servidor.

    id_vivienda = int(partes[1]) if partes[1].isdigit() else None
    id_sensor = datos.get('id_sensor')
    if not isinstance(id_sensor, int) or isinstance(id_sensor, bool) or id_sensor <= 0:
        return
    tipo = partes[2]
    if tipo == 'nivel':
        distancia = _numero(datos.get('distancia_cm'))
        if distancia is None:
            return
        id_tanque = _tanque_de_sensor(id_sensor)
        if id_tanque:
            supabase_client.insert('historial_nivel_tanque', {
                'id_tanque': id_tanque, 'nivel_cm': distancia, 'porcentaje_llenado': _numero(datos.get('porcentaje')),
                'es_valido': True,
            }, return_representation=False)
        _guardar_lectura(id_sensor, id_vivienda, distancia)
        return

    valor = _numero(datos.get('valor'))
    if valor is None:
        return
    datos['valor'] = valor
    _guardar_lectura(id_sensor, id_vivienda, valor)
    if tipo == 'tds' and datos.get('calidad') in CALIDADES_CRITICAS:
        _incidente_calidad(id_sensor, datos)
    elif tipo in ('flujo', 'presion'):
        revisar_lectura(id_vivienda, tipo, valor, id_sensor=id_sensor, alerta_esp32=datos.get('alerta_fuga') is True)


def _cliente():
    import paho.mqtt.client as mqtt

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'smartdrop-ingesta-{os.getpid()}', clean_session=True)
    cliente.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    # Sin setInsecure: se verifica el certificado del broker (con el del sistema o el de MQTT_CA_CERT).
    cliente.tls_set(ca_certs=settings.MQTT_CA_CERT.strip() or None)

    def al_conectar(c, _userdata, _flags, rc, _propiedades=None):
        if not getattr(rc, 'is_failure', rc != 0):
            c.subscribe(TOPICO, qos=1)
            logger.info('Ingesta MQTT conectada y suscrita a %s', TOPICO)
        else:
            logger.error('El broker MQTT rechazó la conexión de la ingesta (código %s)', rc)

    def al_recibir(_c, _userdata, mensaje):
        try:
            procesar_mensaje(mensaje.topic, mensaje.payload.decode('utf-8'))
        except Exception:
            logger.exception('No se pudo procesar el mensaje MQTT de %s', mensaje.topic)
        finally:
            close_old_connections()

    cliente.on_connect = al_conectar
    cliente.on_message = al_recibir
    cliente.reconnect_delay_set(min_delay=1, max_delay=60)
    return cliente


def _bucle():
    espera = 5
    while True:
        try:
            cliente = _cliente()
            cliente.connect(settings.MQTT_BROKER_HOST, int(settings.MQTT_BROKER_PORT or 8883), keepalive=30)
            espera = 5
            cliente.loop_forever(retry_first_connection=True)
        except Exception:
            logger.exception('La ingesta MQTT se desconectó; se reintenta en %s s', espera)
        time.sleep(espera)
        espera = min(espera * 2, 120)


def iniciar():
    """Arranca la ingesta una sola vez por proceso (si está encendida y MQTT está configurado)."""
    global _hilo
    if not getattr(settings, 'MQTT_INGESTA_ENABLED', False):
        return
    if not (settings.MQTT_BROKER_HOST and settings.MQTT_USERNAME and settings.MQTT_PASSWORD):
        logger.error('MQTT_INGESTA_ENABLED=1 pero faltan MQTT_BROKER_HOST, MQTT_USERNAME o MQTT_PASSWORD')
        return
    if _hilo is not None and _hilo.is_alive():
        return
    _hilo = threading.Thread(target=_bucle, name='mqtt-ingesta', daemon=True)
    _hilo.start()
