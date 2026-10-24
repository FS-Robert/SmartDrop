"""Avisos de sensores, alertas y fugas con el texto corto que redacta Gotita.

Se usa en tres lugares: la burbuja de Gotita en la web, las notificaciones de la app y los avisos de
fuga del admin.

Cada aviso se escribe una sola vez y se guarda un rato, y solo unos pocos a la vez, para no gastar de más
cuando la web y la app revisan si hay avisos nuevos.
"""
import hashlib
import logging
from types import SimpleNamespace

from django.core.cache import cache

from .. import preferencias, supabase_client
from ..queries import owner_id, run_parallel
from . import claude, destinos, voz

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 12 * 3600
MAX_REDACCIONES = 3
MAX_FUGAS = 5
DESTINO_SENSOR = {
    'sensor:nivel': 'tanque', 'sensor:presion': 'presion', 'sensor:calidad': 'calidad', 'sensor:flujo': 'consumo',
}


def _amerita(aviso):
    """Solo se escriben los avisos que se van a mostrar: sensores fuera de lo normal y alertas nuevas."""
    if aviso.get('tipo') == 'sensor':
        return aviso.get('estado') not in (None, '', 'Normal')
    return bool(aviso.get('clave'))


def _clave_cache(aviso, rol, idioma):
    # Un sensor se cuenta una vez por estado (alto o bajo), no cada vez que llega una lectura.
    base = f"{aviso.get('clave')}|{aviso.get('estado')}|{rol}|{idioma}"
    if aviso.get('tipo') != 'sensor':
        base += f"|{aviso.get('titulo')}|{aviso.get('mensaje')}"
    return 'gotita:aviso:' + hashlib.sha256(base.encode('utf-8')).hexdigest()[:40]


def _anotar_para_voz(avisos, user):
    """Las frases de los avisos que se le mandan a la persona: así puede pedirlas con la voz de Gotita."""
    if user is None:
        return avisos
    textos = []
    for aviso in avisos:
        textos.append(aviso.get('gotita'))
        textos.append('. '.join(str(parte) for parte in (aviso.get('titulo'), aviso.get('mensaje')) if parte))
    voz.permitir(str(owner_id(user) or 'anonimo'), textos)
    return avisos


def con_texto_de_gotita(avisos, rol, idioma, para=None):
    """Le agrega a cada aviso importante la frase corta de Gotita. Si algo falla, los avisos quedan como estaban.

    `para` es la persona que va a recibir los avisos: sus frases quedan permitidas en la voz natural.
    """
    return _anotar_para_voz(_con_texto(avisos, rol, idioma), para)


def _con_texto(avisos, rol, idioma):
    pendientes = []
    for aviso in avisos:
        if not _amerita(aviso):
            continue
        clave = _clave_cache(aviso, rol, idioma)
        texto = cache.get(clave)
        if texto:
            aviso['gotita'] = texto
        else:
            pendientes.append((aviso, clave))
    if not pendientes or not claude.disponible():
        return avisos
    lote = pendientes[:MAX_REDACCIONES]
    try:
        textos = run_parallel(*[(lambda a=aviso: claude.redactar_aviso(a, rol, idioma)) for aviso, _ in lote])
    except Exception:
        logger.warning('Gotita: no se pudieron redactar los avisos', exc_info=True)
        return avisos
    for (aviso, clave), texto in zip(lote, textos):
        if texto:
            aviso['gotita'] = texto
            cache.set(clave, texto, CACHE_SEGUNDOS)
    return avisos


def idioma_de(user):
    try:
        return preferencias.preferencias_de(user).idioma or 'es'
    except Exception:
        return 'es'


def aviso_de_fuga(alerta):
    """Convierte una alerta de posible fuga al mismo formato de los demás avisos."""
    nic = alerta.get('nic') or 'una vivienda'
    porcentaje = f" ({round(alerta['probabilidad'] * 100)} %)" if alerta.get('probabilidad') is not None else ''
    return {
        'clave': f"fuga:{alerta['id_alerta']}",
        'tipo': 'fuga',
        'categoria': 'fugas',
        'estado': 'nueva',
        'titulo': f'Posible fuga en {nic}{porcentaje}',
        # Nunca el `mensaje` completo de la alerta: trae el nombre y el teléfono del titular, y este aviso se le
        # manda a Claude para redactar la frase de Gotita.
        'mensaje': alerta.get('resumen') or 'El motor de predicción detectó un consumo fuera de lo normal.',
        'detalle': ', '.join(filter(None, [alerta.get('zona'), alerta.get('inicio')])),
        'fecha': alerta.get('fecha'),
        'destino': 'prediccion_fugas',
    }


def agregar_a_alertas_de_fuga(alertas, user):
    """Agrega la frase de Gotita a las alertas de fuga que el admin aún no ha leído."""
    nuevas = [a for a in alertas if not a.get('leida')][:MAX_FUGAS]
    avisos = con_texto_de_gotita([aviso_de_fuga(a) for a in nuevas], destinos.ADMIN, idioma_de(user), para=user)
    for alerta, aviso in zip(nuevas, avisos):
        if aviso.get('gotita'):
            alerta['gotita'] = aviso['gotita']
    return alertas


def _fugas_admin(user):
    """Avisos de fuga sin leer del admin."""
    from ml_engine.views_ops import LeakAlertsView

    datos = LeakAlertsView().get(SimpleNamespace(user=user, query_params={'solo_no_leidas': '1'})).data
    return [aviso_de_fuga(alerta) for alerta in (datos.get('alertas') or [])[:MAX_FUGAS]]


def avisos_para(user):
    """Avisos actuales de la persona (o del admin) con la frase de Gotita, para la burbuja de la web."""
    from ..api.views_mobile_profile import _avisos_de_sensores, _avisos_guardados

    rol = destinos.rol_de(user)
    idioma = idioma_de(user)
    try:
        if rol == destinos.ADMIN:
            avisos = _fugas_admin(user)
        else:
            peticion = SimpleNamespace(user=user)
            prefs = preferencias.preferencias_de(user)
            avisos = _avisos_de_sensores(peticion, prefs) + _avisos_guardados(peticion, prefs)
            for aviso in avisos:
                aviso['destino'] = DESTINO_SENSOR.get(aviso.get('clave'), 'alertas')
    except supabase_client.SupabaseError:
        logger.warning('Gotita: no se pudieron leer los avisos', exc_info=True)
        return []
    return con_texto_de_gotita(avisos, rol, idioma, para=user)
