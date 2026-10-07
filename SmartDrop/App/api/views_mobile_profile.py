"""API móvil del perfil: datos personales, preferencias (las mismas de la web) y avisos para notificaciones push."""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import preferencias, supabase_client
from ..models import Usuario
from ..queries import invalidate_user_viviendas, is_admin, owner_id, user_viviendas
from .permissions import IsAuthenticatedUser
from .views_mobile_data import _matching_sensor, _reading_series, _visible_data

logger = logging.getLogger(__name__)

AVISOS_DIAS = 7
ROLES = {'admin': 'Administrador del sistema', 'user': 'Usuario'}
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre')
SENSOR_PARAMETROS = (
    ('nivel', 'Nivel del tanque', ('nivel', 'level')),
    ('presion', 'Presión', ('presion', 'pressure')),
    ('flujo', 'Flujo de agua', ('flujo', 'flow')),
    ('calidad', 'Calidad del agua', ('calidad', 'tds')),
)


def _usuario_remoto(user):
    rows = supabase_client.select(
        'usuario', 'id_usuario,nombre,apellido,correo,fecha_registro,id_rol',
        {'id_usuario': f'eq.{owner_id(user)}', 'limit': '1'},
    )
    return rows[0] if rows else {}


def _miembro_desde(raw):
    try:
        fecha = timezone.datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return ''
    return f'{MESES[fecha.month - 1]} {fecha.year}'


def _perfil(user, prefs=None):
    prefs = prefs or preferencias.preferencias_de(user)
    remoto = _usuario_remoto(user)
    try:
        viviendas = user_viviendas(user)
    except supabase_client.SupabaseError:
        viviendas = []
    nombre = remoto.get('nombre') or ''
    apellido = remoto.get('apellido') or ''
    completo = f'{nombre} {apellido}'.strip()
    rol = getattr(user, 'nombre_rol', None) or ('admin' if is_admin(user) else 'user')
    return {
        'id_usuario': owner_id(user),
        'nombre': nombre,
        'apellido': apellido,
        'nombre_completo': completo or remoto.get('correo') or getattr(user, 'email', ''),
        'iniciales': ''.join(part[0] for part in completo.split()[:2]).upper() or (getattr(user, 'email', '?')[:1].upper()),
        'correo': remoto.get('correo') or getattr(user, 'email', ''),
        'rol': rol,
        'rol_label': ROLES.get(rol, rol),
        'es_admin': is_admin(user),
        'direccion': viviendas[0].get('direccion') if viviendas else '',
        'telefono': next((v.get('telefono_titular') for v in viviendas if v.get('telefono_titular')), '') or '',
        'miembro_desde': _miembro_desde(remoto.get('fecha_registro')),
        'preferencias': preferencias.como_dict(prefs),
        'reporte_semanal': preferencias.reporte_semanal(user, prefs),
    }


class MobilePerfilView(APIView):
    """GET: perfil completo. PATCH/PUT: editar nombre, apellido y correo."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        try:
            return Response(_perfil(request.user))
        except supabase_client.SupabaseError:
            logger.exception('No se pudo cargar el perfil móvil')
            return Response({'error': 'No se pudo cargar el perfil.'}, status=status.HTTP_502_BAD_GATEWAY)

    def patch(self, request):
        nombre = str(request.data.get('nombre', '')).strip()
        apellido = str(request.data.get('apellido', '')).strip()
        correo = str(request.data.get('correo', '')).strip().lower()
        errores = {}
        if not nombre:
            errores['nombre'] = 'El nombre es obligatorio.'
        if not apellido:
            errores['apellido'] = 'El apellido es obligatorio.'
        try:
            validate_email(correo)
        except ValidationError:
            errores['correo'] = 'Escribe un correo electrónico válido.'
        if errores:
            return Response({'error': 'Revisa los datos.', 'errores': errores}, status=status.HTTP_400_BAD_REQUEST)

        user_id = owner_id(request.user)
        try:
            existente = supabase_client.get_user_by_email(correo)
            if existente and existente.get('id_usuario') != user_id:
                return Response({'error': 'Este correo ya está registrado.', 'errores': {'correo': 'Este correo ya está registrado.'}},
                                status=status.HTTP_409_CONFLICT)
            actualizado = supabase_client.update(
                'usuario', {'nombre': nombre, 'apellido': apellido, 'correo': correo},
                {'id_usuario': f'eq.{user_id}'}, return_representation=True,
            )
        except supabase_client.SupabaseError:
            logger.exception('No se pudo actualizar el perfil móvil')
            return Response({'error': 'No se pudo guardar la información.'}, status=status.HTTP_502_BAD_GATEWAY)
        if not actualizado:
            return Response({'error': 'No se recibió confirmación al guardar.'}, status=status.HTTP_502_BAD_GATEWAY)

        # La sesión web usa una copia local del usuario: se mantiene igual a Supabase.
        Usuario.objects.filter(supabase_id=user_id).update(nombre=nombre, apellido=apellido, email=correo)
        invalidate_user_viviendas(request.user)
        return Response({'ok': True, 'mensaje': 'Tu información personal se actualizó correctamente.', 'perfil': _perfil(request.user)})

    put = patch


class MobilePreferenciasView(APIView):
    """GET: preferencias. PATCH/POST: cambia uno o varios campos (los mismos de la web)."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        prefs = preferencias.preferencias_de(request.user)
        return Response({'ok': True, 'preferencias': preferencias.como_dict(prefs),
                         'reporte_semanal': preferencias.reporte_semanal(request.user, prefs)})

    def patch(self, request):
        prefs, errores = preferencias.actualizar(request.user, request.data)
        body = {'ok': not errores, 'preferencias': preferencias.como_dict(prefs),
                'reporte_semanal': preferencias.reporte_semanal(request.user, prefs)}
        if errores:
            body['errores'] = errores
            return Response(body, status=status.HTTP_400_BAD_REQUEST)
        return Response(body)

    post = patch


def _avisos_de_sensores(request, prefs):
    """Estado actual de cada parámetro de la vivienda del usuario (solo categorías activadas)."""
    if is_admin(request.user):
        return []  # El admin recibe los avisos de fuga del motor de predicción (otra ruta).
    _, sensores, lecturas = _visible_data(request)
    avisos = []
    for parametro, nombre, nombres in SENSOR_PARAMETROS:
        if not preferencias.sensor_habilitado(prefs, parametro):
            continue
        sensor = next((s for s in sensores if any(n in str(s.get('tipo_sensor') or '').lower() for n in nombres)), None)
        if parametro != 'calidad':
            sensor = _matching_sensor(sensores, parametro) or sensor
        serie = _reading_series(sensor, lecturas)
        if not serie:
            continue
        ultima = serie[-1]
        estado = ultima.get('estado', 'Normal')
        unidad = (sensor or {}).get('unidad_medida') or ''
        avisos.append({
            'clave': f'sensor:{parametro}',
            'tipo': 'sensor',
            'categoria': preferencias.SENSOR_CATEGORIES[parametro],
            'estado': estado,
            'titulo': f'{nombre}: valor {"alto" if estado == "Alta" else "bajo"}' if estado != 'Normal' else nombre,
            'mensaje': f'Lectura actual: {ultima["valor"]:.2f} {unidad}'.strip(),
            'fecha': ultima.get('fecha'),
        })
    return avisos


def _avisos_guardados(request, prefs):
    """Alertas dirigidas al usuario (consumo elevado, reporte semanal…) de los últimos días, sin leer."""
    user_id = owner_id(request.user)
    desde = (timezone.now() - timedelta(days=AVISOS_DIAS)).isoformat()
    notificaciones = supabase_client.select('notificacion', 'id_alerta,estado_visualizacion,fecha_envio', {
        'id_usario_destino': f'eq.{user_id}', 'fecha_envio': f'gte.{desde}',
        'estado_visualizacion': 'eq.no_leida', 'order': 'fecha_envio.desc', 'limit': '50',
    })
    ids = [str(n['id_alerta']) for n in notificaciones if n.get('id_alerta')]
    if not ids:
        return []
    alertas = supabase_client.select('alerta', 'id_alerta,tipo_alerta,mensaje,fecha_creacion,datos_adicionales', {
        'id_alerta': f"in.({','.join(ids)})", 'limit': str(len(ids)),
    })
    avisos = []
    for alerta in preferencias.filtrar_alertas(alertas, prefs):
        extra = alerta.get('datos_adicionales') or {}
        if extra.get('origen') == 'ml_engine':
            continue  # Avisos de fuga: la app ya los notifica por su cuenta.
        tipo = alerta.get('tipo_alerta') or ''
        avisos.append({
            'clave': f"alerta:{alerta['id_alerta']}",
            'tipo': 'alerta',
            'categoria': preferencias.categoria_alerta(tipo) or tipo,
            'estado': 'nueva',
            'titulo': 'Reporte semanal de consumo' if tipo == preferencias.WEEKLY_REPORT_ORIGIN
            else tipo.replace('_', ' ').capitalize() or 'Alerta',
            'mensaje': (alerta.get('mensaje') or '').split('\n')[0],
            'fecha': alerta.get('fecha_creacion'),
        })
    return avisos


class MobileNotificacionesView(APIView):
    """GET: avisos para las notificaciones push de la app, según las preferencias del usuario.

    `sensor:*` trae el estado actual de cada parámetro (la app avisa cuando pasa de Normal a Alta/Baja);
    `alerta:*` son alertas nuevas dirigidas al usuario (la app avisa una sola vez por id).
    """
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        prefs = preferencias.preferencias_de(request.user)
        preferencias.reporte_semanal(request.user, prefs)
        try:
            avisos = _avisos_de_sensores(request, prefs) + _avisos_guardados(request, prefs)
        except supabase_client.SupabaseError:
            logger.exception('No se pudieron preparar los avisos móviles')
            return Response({'error': 'No se pudieron cargar los avisos.'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'ok': True, 'preferencias': preferencias.como_dict(prefs), 'avisos': avisos})
