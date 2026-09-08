import os

from paho.mqtt import MQTTException


class MqttError(Exception):
    """Error controlado al publicar un comando MQTT."""


def publish_command(topic: str, payload: str) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise MqttError('Falta instalar la dependencia paho-mqtt.') from exc

    host = os.environ.get('MQTT_BROKER_HOST', '').strip()
    username = os.environ.get('MQTT_USERNAME', '').strip()
    password = os.environ.get('MQTT_PASSWORD', '')
    if not host or not username or not password:
        raise MqttError('MQTT no está configurado en el entorno del servidor.')

    client = None
    try:
        port = int(os.environ.get('MQTT_BROKER_PORT', '8883'))
        client = mqtt.Client(client_id=f'smartdrop-web-{os.getpid()}')
        client.username_pw_set(username, password)
        ca_cert = os.environ.get('MQTT_CA_CERT', '').strip() or None
        client.tls_set(ca_certs=ca_cert)
        client.connect(host, port, keepalive=10)
        result = client.publish(topic, payload, qos=1)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttError(f'El broker rechazó el comando (código {result.rc}).')
        try:
            result.wait_for_publish(timeout=5)
        except RuntimeError:
            # El broker aceptó el envío; la confirmación puede llegar después.
            pass
    except (OSError, ValueError, MQTTException) as exc:
        raise MqttError(f'No se pudo publicar el comando MQTT: {exc}') from exc
    finally:
        if client is not None:
            client.disconnect()