"""Los datos del agua que Gotita puede consultar en cada pregunta (solo para leer, se guardan un minuto).

Usuario: el estado del agua de SU vivienda (las mismas consultas que la app) y sus alertas sin leer.
Admin: resumen de posibles fugas, riesgo de desabasto por zona y reportes por estado.
Nunca se incluyen correos, teléfonos, direcciones de otras personas ni nada de la válvula.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

from .. import preferencias, supabase_client
from ..queries import owner_id, users_by_id
from . import destinos

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 60
TIEMPO_MAXIMO = 6.0
MAX_ALERTAS = 5
MAX_FUGAS = 3

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='gotita-datos')


def hora_local(valor):
    """Fecha y hora de El Salvador en forma corta (día/mes hora:minutos)."""
    if not valor:
        return None
    try:
        fecha = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(fecha):
        fecha = fecha.replace(tzinfo=dt_timezone.utc)
    return timezone.localtime(fecha).strftime('%d/%m %H:%M')


def _redondear(valor, decimales=1):
    try:
        return round(float(valor), decimales)
    except (TypeError, ValueError):
        return None


def _metrica(bloque, *extras):
    if not bloque:
        return None
    resultado = {
        'valor': _redondear(bloque.get('valor'), 2),
        'unidad': bloque.get('unidad') or '',
        'estado': bloque.get('estado'),
        'hora': hora_local(bloque.get('fecha')),
    }
    for campo in extras:
        if bloque.get(campo) is not None:
            resultado[campo] = bloque[campo]
    return resultado


def _datos_usuario(user):
    from ..api.views_mobile_data import MobileEstadoAguaView
    from ..api.views_mobile_profile import _avisos_guardados

    peticion = SimpleNamespace(user=user)
    prefs = preferencias.preferencias_de(user)
    estado = MobileEstadoAguaView().get(peticion).data
    try:
        alertas = _avisos_guardados(peticion, prefs)[:MAX_ALERTAS]
    except supabase_client.SupabaseError:
        alertas = []
    vivienda = estado.get('vivienda') or {}
    return {
        'vivienda_nic': vivienda.get('nic'),
        'resumen': (estado.get('resumen_general') or {}).get('mensaje'),
        'nivel_tanque': _metrica(estado.get('nivel'), 'porcentaje', 'litros_disponibles', 'capacidad_maxima_litros'),
        'presion': _metrica(estado.get('presion')),
        'calidad_tds': _metrica(estado.get('calidad')),
        'consumo_hoy_litros': _redondear((estado.get('consumo') or {}).get('litros_hoy')),
        'alertas_sin_leer': [
            {'titulo': a.get('titulo'), 'mensaje': a.get('mensaje'), 'hora': hora_local(a.get('fecha'))}
            for a in alertas
        ],
    }


def _resumen_fugas():
    from ml_engine.leaks.detect import alert_threshold
    from ml_engine.models import LeakPrediction

    umbral = alert_threshold()
    ultimas = {}
    for prediccion in LeakPrediction.objects.select_related('home').order_by('generated_at'):
        ultimas[prediccion.home_id] = prediccion
    posibles = sorted((p for p in ultimas.values() if p.probabilidad >= umbral), key=lambda p: -p.probabilidad)
    return {
        'viviendas_evaluadas': len(ultimas),
        'posibles_fugas': len(posibles),
        'principales': [
            {
                'nic': (p.home.meta or {}).get('nic', p.home.etiqueta),
                'zona': (p.home.meta or {}).get('zona', ''),
                'probabilidad_pct': p.porcentaje,
                'hora': hora_local(p.generated_at),
            }
            for p in posibles[:MAX_FUGAS]
        ],
    }


def _resumen_desabasto():
    from ml_engine.models import ShortagePrediction

    zonas = {}
    for prediccion in ShortagePrediction.objects.select_related('zone').order_by('generated_at'):
        zonas[prediccion.zone_id] = prediccion
    return [
        {
            'zona': p.zone.name,
            'riesgo': p.nivel_riesgo,
            'horas_hasta_desabasto': _redondear(p.median_hours_to_shortage, 0),
            'hora': hora_local(p.generated_at),
        }
        for p in zonas.values()
    ]


def _resumen_reportes():
    filas = supabase_client.select('reporte', 'estado,fecha_reporte', {'order': 'fecha_reporte.desc', 'limit': '500'})
    hoy = timezone.localdate().strftime('%d/%m')
    conteo = {'pendiente': 0, 'en_proceso': 0, 'resuelto': 0}
    for fila in filas:
        if fila.get('estado') in conteo:
            conteo[fila['estado']] += 1
    return {**conteo, 'nuevos_hoy': sum(1 for f in filas if (hora_local(f.get('fecha_reporte')) or '').startswith(hoy))}


def _parte(funcion):
    try:
        return funcion()
    except Exception:  # Un dato que falla no debe dejar a Gotita sin responder.
        logger.warning('Gotita: no se pudo leer %s', funcion.__name__, exc_info=True)
        return None


def _datos_admin():
    return {
        'fugas': _parte(_resumen_fugas),
        'desabasto_por_zona': _parte(_resumen_desabasto),
        'reportes': _parte(_resumen_reportes),
    }


def _con_conexion_fresca(funcion, *args):
    """Los hilos del pool no pasan por el ciclo de una petición: sin esto, una conexión a la base que el servidor
    cerró por inactividad (Postgres) se quedaba rota y los datos del admin salían vacíos hasta reiniciar."""
    close_old_connections()
    try:
        return funcion(*args)
    finally:
        close_old_connections()


def datos_de(user, rol):
    """Datos para Gotita, o None si no se pudieron leer a tiempo."""
    clave = f'gotita:datos:{rol}:{owner_id(user)}'
    datos = cache.get(clave)
    if datos is not None:
        return datos
    tarea = _pool.submit(_con_conexion_fresca, _datos_admin if rol == destinos.ADMIN else _datos_usuario,
                         *([] if rol == destinos.ADMIN else [user]))
    try:
        datos = tarea.result(timeout=TIEMPO_MAXIMO)
    except FuturesTimeout:
        logger.warning('Gotita: los datos tardaron más de %ss', TIEMPO_MAXIMO)
        return None
    except Exception:
        logger.warning('Gotita: no se pudieron leer los datos', exc_info=True)
        return None
    cache.set(clave, datos, CACHE_SEGUNDOS)
    return datos


_LETRAS_NOMBRE = re.compile(r"[^\w\s'\-]", re.UNICODE)


def _limpiar_nombre(valor):
    # Solo letras y espacios, cortito: el nombre lo escribe la persona y no debe colarse como instrucción.
    texto = _LETRAS_NOMBRE.sub(' ', str(valor or ''))
    texto = re.sub(r'[\d_]+', '', texto)
    return ' '.join(texto.split())[:40]


def _buscar_nombre(id_usuario):
    from ..models import Usuario
    local = Usuario.objects.filter(supabase_id=id_usuario).values_list('nombre', flat=True).first()
    if local:
        return local
    fila = users_by_id([id_usuario], 'id_usuario,nombre').get(int(id_usuario)) or {}
    return fila.get('nombre')


def nombre_de(user):
    """Nombre de pila de quien está con la sesión abierta ('' si no se sabe)."""
    directo = getattr(user, 'nombre', None)
    if directo:
        return _limpiar_nombre(directo)
    id_usuario = owner_id(user)
    if not id_usuario:
        return ''
    clave = f'gotita:nombre:{id_usuario}'
    nombre = cache.get(clave)
    if nombre is None:
        try:
            nombre = _limpiar_nombre(_buscar_nombre(id_usuario))
        except Exception:
            logger.warning('Gotita: no se pudo leer el nombre de la cuenta %s', id_usuario, exc_info=True)
            nombre = ''
        cache.set(clave, nombre, 3600)
    return nombre
