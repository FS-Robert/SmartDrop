"""Protección automática de las válvulas por nivel del tanque (PB052) y por calidad del agua.

Reglas que pidió el equipo:

* Nivel: bajo el 10 % se avisa al administrador. Con 3 lecturas seguidas en 8 % o menos se cierran las
  válvulas abiertas y el botón "Abrir válvula" queda bloqueado ("Bloqueado - Nivel insuficiente").
  Con 3 lecturas seguidas sobre el 30 % se quita ese bloqueo.
* Calidad: con 3 lecturas seguidas "Pobre" o "Inaceptable" se cierran las válvulas y se bloquean
  ("Bloqueado - Calidad del agua pobre"), aunque el tanque esté lleno, y se pide revisar y filtrar el agua.
  Con 3 lecturas seguidas de agua apta se quita ese bloqueo.
* La válvula nunca se abre sola: al quitarse todos los bloqueos solo se vuelve a habilitar el botón y se avisa.

Todo corre en el servidor con las lecturas que ya manda el ESP32; no hace falta cambiar el ESP32.
Como un mismo tanque abastece a la red, el bloqueo aplica a todas las válvulas.
"""
import logging
import threading

from django.utils import timezone

from . import supabase_client

logger = logging.getLogger(__name__)

NIVEL_AVISO = 10       # % : bajo este nivel se avisa al administrador
NIVEL_CIERRE = 8       # % : en este nivel o menos se cierra y se bloquea
NIVEL_HABILITA = 30    # % : sobre este nivel se quita el bloqueo por nivel
LECTURAS_SEGUIDAS = 3  # lecturas iguales seguidas para no reaccionar a una lectura falsa
CALIDADES_MALAS = ('pobre', 'inaceptable', 'muy mala')

MENSAJES = {
    'nivel': ('Bloqueado - Nivel insuficiente',
              f'Se habilitará cuando el nivel del tanque pase del {NIVEL_HABILITA} %.'),
    'calidad': ('Bloqueado - Calidad del agua pobre',
                'Se habilitará cuando el agua vuelva a ser apta. Revisen el tanque y filtren el agua.'),
}

# Cuántas lecturas seguidas van en cada situación (se reinicia cuando llega una lectura distinta).
_seguidas = {}
_LOCK = threading.Lock()


class ValvulaBloqueada(Exception):
    """Se pidió abrir una válvula mientras hay un bloqueo por nivel o por calidad."""


# ── Estado guardado ──────────────────────────────────────────────────────────

def _fila(motivo):
    from .models import BloqueoValvula
    fila, _ = BloqueoValvula.objects.get_or_create(motivo=motivo)
    return fila


def bloqueos_activos():
    """Lista de bloqueos vigentes con su título y su aviso, para mostrarlos en la web y en la app."""
    from .models import BloqueoValvula
    try:
        activos = list(BloqueoValvula.objects.filter(activo=True, motivo__in=MENSAJES).order_by('motivo'))
    except Exception:
        logger.exception('No se pudo leer el bloqueo de las válvulas')
        return []
    return [{
        'motivo': fila.motivo,
        'titulo': MENSAJES[fila.motivo][0],
        'aviso': MENSAJES[fila.motivo][1],
        'valor': fila.valor,
        'desde': fila.desde,
    } for fila in activos]


def mensaje_bloqueo(bloqueos=None):
    """Texto corto con todos los bloqueos vigentes, o '' si se puede abrir."""
    bloqueos = bloqueos_activos() if bloqueos is None else bloqueos
    return ' '.join(f"{b['titulo']}. {b['aviso']}" for b in bloqueos)


def revisar_apertura():
    """Lanza ValvulaBloqueada si hoy no se permite abrir ninguna válvula."""
    mensaje = mensaje_bloqueo()
    if mensaje:
        raise ValvulaBloqueada(mensaje)


def _activar(motivo, valor):
    fila = _fila(motivo)
    if fila.activo:
        fila.valor = valor
        fila.save(update_fields=['valor', 'actualizado'])
        return False
    fila.activo, fila.valor, fila.desde = True, valor, timezone.now()
    fila.save()
    return True


def _desactivar(motivo):
    fila = _fila(motivo)
    if not fila.activo:
        return False
    fila.activo = False
    fila.save(update_fields=['activo', 'actualizado'])
    return True


def _contar(clave, cumple):
    """Suma una lectura a la racha `clave` si `cumple`; si no, la reinicia. Devuelve cuántas van."""
    with _LOCK:
        _seguidas[clave] = _seguidas.get(clave, 0) + 1 if cumple else 0
        return _seguidas[clave]


# ── Avisos ───────────────────────────────────────────────────────────────────

def _usuarios(solo_admins):
    filtros = {'estado_usuario': 'eq.true'}
    if solo_admins:
        filtros['id_rol'] = 'eq.2'
    return [fila['id_usuario'] for fila in supabase_client.select('usuario', 'id_usuario,id_rol', filtros)]


def _avisar(tipo_alerta, titulo, mensaje, *, solo_admins, prioridad='alta'):
    """Crea la alerta y una notificación para cada destinatario (así llega a la app como aviso)."""
    try:
        alerta = supabase_client.insert('alerta', {
            'tipo_alerta': tipo_alerta,
            'prioridad': prioridad,
            'mensaje': mensaje,
            'estado_confirmacion': 'no_confirmada',
            'fecha_creacion': timezone.now().isoformat(),
            'datos_adicionales': {'origen': 'proteccion_agua', 'titulo': titulo},
        }) or {}
        if not alerta.get('id_alerta'):
            return
        ahora = timezone.now().isoformat()
        for id_usuario in _usuarios(solo_admins):
            supabase_client.insert('notificacion', {
                'id_alerta': alerta['id_alerta'],
                'id_usario_destino': id_usuario,
                'canal_envio': 'dashboard',
                'estado_visualizacion': 'no_leida',
                'fecha_envio': ahora,
            }, return_representation=False)
    except Exception:
        # Un aviso que no sale no debe impedir que la válvula se cierre.
        logger.exception('No se pudo enviar el aviso "%s"', titulo)


def _avisar_si_ya_se_puede_abrir(motivo_resuelto):
    if bloqueos_activos():
        otro = mensaje_bloqueo()
        _avisar('valvula_bloqueo', f'{motivo_resuelto}, pero la válvula sigue bloqueada',
                f'{motivo_resuelto}. La válvula sigue bloqueada: {otro}', solo_admins=True, prioridad='media')
        return
    _avisar('valvula_bloqueo', 'Válvula disponible para operar',
            f'{motivo_resuelto}. La válvula ya se puede abrir desde el panel de administración.',
            solo_admins=False, prioridad='media')


# ── Reglas ───────────────────────────────────────────────────────────────────

def revisar_nivel(porcentaje):
    """Aplica las reglas de nivel con una lectura del tanque (en %)."""
    if porcentaje is None:
        return
    porcentaje = float(porcentaje)
    texto = f'{porcentaje:.0f} %'

    if porcentaje < NIVEL_AVISO and _activar('aviso_nivel', texto):
        _avisar('nivel_tanque', 'Nivel bajo del tanque',
                f'El tanque está al {texto}. Si llega al {NIVEL_CIERRE} % la válvula se cerrará sola.',
                solo_admins=True, prioridad='media')

    if _contar('nivel_bajo', porcentaje <= NIVEL_CIERRE) >= LECTURAS_SEGUIDAS:
        nuevo = _activar('nivel', texto)
        cerradas = cerrar_abiertas(f'Cierre automático - Nivel bajo del tanque ({texto})')
        if nuevo:
            _avisar('nivel_tanque', 'Suministro suspendido',
                    f'Suministro suspendido - Nivel bajo del tanque ({texto}). '
                    f'Se reanudará cuando el tanque se reabastezca y pase del {NIVEL_HABILITA} %.',
                    solo_admins=False)
            logger.warning('Bloqueo por nivel bajo (%s); válvulas cerradas: %s', texto, cerradas)

    if _contar('nivel_recuperado', porcentaje > NIVEL_HABILITA) >= LECTURAS_SEGUIDAS:
        _desactivar('aviso_nivel')
        if _desactivar('nivel'):
            _avisar_si_ya_se_puede_abrir(f'Nivel recuperado ({texto})')


def revisar_calidad(calidad, ppm=None):
    """Aplica las reglas de calidad con una lectura del sensor TDS."""
    if not calidad:
        return
    mala = str(calidad).strip().lower() in CALIDADES_MALAS
    texto = f'{calidad}, {ppm:.0f} ppm' if isinstance(ppm, (int, float)) else str(calidad)

    if _contar('calidad_mala', mala) >= LECTURAS_SEGUIDAS:
        nuevo = _activar('calidad', texto)
        cerradas = cerrar_abiertas(f'Cierre automático - Calidad del agua {calidad} ({texto})')
        if nuevo:
            _avisar('calidad_agua', 'Calidad del agua no apta',
                    f'La calidad del agua es {texto}. La válvula se cerró y queda bloqueada aunque el tanque '
                    'esté lleno. Revisen el tanque y filtren el agua; se habilitará cuando el agua vuelva a ser apta.',
                    solo_admins=True)
            logger.warning('Bloqueo por calidad del agua (%s); válvulas cerradas: %s', texto, cerradas)

    if _contar('calidad_buena', not mala) >= LECTURAS_SEGUIDAS:
        if _desactivar('calidad'):
            _avisar_si_ya_se_puede_abrir(f'El agua vuelve a ser apta ({texto})')


# ── Cierre ───────────────────────────────────────────────────────────────────

def cerrar_abiertas(razon, publish=None):
    """Cierra todas las válvulas que estén abiertas y lo deja en `log_valvula`. Devuelve sus ids."""
    from . import valve_service
    from .mqtt_service import publish_command

    publish = publish or publish_command
    cerradas = []
    try:
        valvulas = supabase_client.select(
            'valvula', 'id_valvula,estado_actual,topic_mqtt_comando,ultima_apertura', {'order': 'id_valvula'})
    except Exception:
        logger.exception('No se pudieron leer las válvulas para el cierre automático')
        return cerradas
    for valvula in valvulas:
        if not valve_service.esta_abierta(valvula.get('estado_actual')) or not valvula.get('topic_mqtt_comando'):
            continue
        try:
            valve_service.cancel_timer(valvula['id_valvula'])
            publish(valvula['topic_mqtt_comando'], 'cerrar')
            supabase_client.update('valvula', {'estado_actual': 'cerrada', 'ultima_apertura': None},
                                   {'id_valvula': f"eq.{int(valvula['id_valvula'])}"})
            valve_service.insertar_log({
                'id_valvula': valvula['id_valvula'],
                'accion': 'cerrar',
                'estado_anterior': valvula.get('estado_actual') or 'desconocida',
                'estado_nuevo': 'cerrada',
                'tipo_activacion': 'automatico',
                'id_usuario': None,
                'fecha_hora': timezone.now().isoformat(),
                'ip_dispositivo': 'proteccion',
                'origen_accion': 'servidor',
                'duracion_programada': None,
                'duracion_real': valve_service._segundos_abierta(valvula),
                'razon': razon,
            })
            cerradas.append(valvula['id_valvula'])
        except Exception:
            # Se vuelve a intentar con la siguiente lectura, porque el bloqueo sigue activo.
            logger.exception('No se pudo cerrar automáticamente la válvula %s', valvula.get('id_valvula'))
    return cerradas
