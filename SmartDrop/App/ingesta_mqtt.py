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


# Lo que cada sensor puede medir de verdad. Una lectura fuera de estos límites es ruido o un cable flojo
# (por ejemplo 3000 L/min en el sensor de flujo) y no se guarda, para que no aparezca en la web ni en la app.
LIMITES_LECTURA = {
    'flujo': (0.0, 30.0),       # YF-S201: hasta 30 L/min
    'presion': (0.0, 700.0),    # MPX5700AP: 0 a 700 kPa
    'tds': (0.0, 2000.0),       # sensor TDS en ppm (desde 1200 ya es "Inaceptable", pero se guarda para avisar)
    'nivel': (0.0, 450.0),      # centímetros
}
# Nombre del sensor en Supabase -> nombre corto de arriba.
TIPOS_SENSOR = {'flujo': 'flujo', 'flow': 'flujo', 'presion': 'presion', 'presión': 'presion',
                'calidad': 'tds', 'tds': 'tds', 'nivel': 'nivel', 'level': 'nivel'}


def lectura_imposible(tipo, valor):
    """True si el valor no puede venir de un sensor que funciona bien."""
    limites = LIMITES_LECTURA.get(TIPOS_SENSOR.get(str(tipo or '').lower(), str(tipo or '').lower()))
    if not limites or valor is None:
        return False
    return not limites[0] <= valor <= limites[1]


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero and abs(numero) != float('inf') else None


def _tanque_de_sensor(id_sensor):
    """Tanque al que pertenece un sensor de nivel y su altura (antes estaba fijo en consumer.js: sensor 2 -> tanque 1)."""
    filas = supabase_client.select('tanque', 'id_tanque,altura_total', {'id_sensor_nivel': f'eq.{int(id_sensor)}', 'order': 'id_tanque', 'limit': '1'})
    return filas[0] if filas else None


def _agua_cm(porcentaje, altura_total):
    """Centímetros de agua que se guardan en `lectura` para que la web y la app calculen bien el %.

    El ESP32 manda dos cosas: la distancia del sensor al agua y el % ya calculado con las medidas del tambo.
    La web y la app leen `lectura` como "cm de agua sobre la altura del tanque", así que se guarda ese % pasado
    a cm. Si se guardara la distancia, el tambo lleno (agua a 3 cm del sensor) se vería casi vacío.
    """
    if porcentaje is None or not altura_total:
        return None
    return round(min(max(porcentaje, 0), 100) * altura_total / 100, 2)


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
        porcentaje = _numero(datos.get('porcentaje'))
        if lectura_imposible('nivel', distancia) or (porcentaje is not None and not 0 <= porcentaje <= 100):
            logger.warning('Lectura de nivel imposible descartada (sensor %s): %s cm, %s %%', id_sensor, distancia, porcentaje)
            return
        tanque = _tanque_de_sensor(id_sensor)
        if tanque:
            supabase_client.insert('historial_nivel_tanque', {
                'id_tanque': tanque['id_tanque'], 'nivel_cm': distancia, 'porcentaje_llenado': porcentaje,
                'es_valido': True,
            }, return_representation=False)
        agua = _agua_cm(porcentaje, _numero((tanque or {}).get('altura_total')))
        if agua is None:
            # Sin % o sin altura del tanque no se puede saber cuánta agua hay: mejor no guardar un dato al revés.
            logger.warning('Lectura de nivel sin porcentaje o sin altura del tanque (sensor %s)', id_sensor)
            return
        _guardar_lectura(id_sensor, id_vivienda, agua)
        return

    valor = _numero(datos.get('valor'))
    if valor is None:
        return
    if lectura_imposible(tipo, valor):
        logger.warning('Lectura imposible descartada (%s, sensor %s): %s', tipo, id_sensor, valor)
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
