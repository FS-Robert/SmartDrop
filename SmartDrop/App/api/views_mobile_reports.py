"""API móvil del sistema de reportes: crear reportes con evidencia
fotográfica/video (multipart), listar reportes propios y de la comunidad,
ver el detalle con adjuntos y chatear con los administradores.
"""
import logging

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import supabase_client
from ..views_reportes import (
    ESTADO_LABEL,
    MAX_ADJUNTOS,
    _guardar_adjuntos,
    _tipo_meta,
)
from .permissions import IsAuthenticatedUser

logger = logging.getLogger(__name__)


def _user_id(user):
    return getattr(user, 'supabase_id', None) or getattr(user, 'id_usuario', None)


def _es_admin(user):
    return getattr(user, 'rol_id', None) == 2


def _serialize_reporte(row, adjuntos_map=None, autores=None):
    adjuntos = (adjuntos_map or {}).get(row.get('id_reporte'), [])
    return {
        'id_reporte': row.get('id_reporte'),
        'tipo_problema': row.get('tipo_problema'),
        'tipo_label': _tipo_meta().get(row.get('tipo_problema'), {}).get('label', row.get('tipo_problema')),
        'descripcion': row.get('descripcion'),
        'ubicacion': row.get('ubicacion'),
        'estado': row.get('estado'),
        'estado_label': ESTADO_LABEL.get(row.get('estado'), row.get('estado')),
        'prioridad': row.get('prioridad'),
        'fecha_reporte': row.get('fecha_reporte'),
        'respuesta_admin': row.get('respuesta_admin'),
        'autor_nombre': (autores or {}).get(row.get('id_usuario'), ''),
        'adjuntos': [
            {'id_adjunto': a.get('id_adjunto'), 'url': a.get('url'), 'tipo': a.get('tipo'), 'nombre': a.get('nombre')}
            for a in adjuntos
        ],
        'n_adjuntos': len(adjuntos),
    }


def _adjuntos_map(reporte_ids):
    if not reporte_ids:
        return {}
    ids = ','.join(str(i) for i in reporte_ids)
    try:
        rows = supabase_client.select(
            'reporte_adjunto', '*',
            {'id_reporte': f'in.({ids})', 'order': 'id_adjunto.asc', 'limit': '500'},
        )
    except supabase_client.SupabaseError:
        return {}
    agrupados = {}
    for row in rows:
        agrupados.setdefault(row.get('id_reporte'), []).append(row)
    return agrupados


def _nombres_usuarios(ids):
    if not ids:
        return {}
    try:
        rows = supabase_client.select(
            'usuario', 'id_usuario,nombre,apellido,id_rol',
            {'id_usuario': f"in.({','.join(str(i) for i in ids)})", 'limit': '500'},
        )
    except supabase_client.SupabaseError:
        return {}
    return {
        row['id_usuario']: {
            'nombre': f"{row.get('nombre', '')} {row.get('apellido', '')}".strip(),
            'es_admin': row.get('id_rol') == 2,
        }
        for row in rows
    }


class MobileReportesView(APIView):
    """GET: mis reportes. POST: crear reporte (multipart con adjuntos)."""
    permission_classes = [IsAuthenticatedUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        try:
            rows = supabase_client.select(
                'reporte', '*',
                {'id_usuario': f'eq.{_user_id(request.user)}', 'order': 'fecha_reporte.desc', 'limit': '100'},
            )
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        adjuntos = _adjuntos_map([r.get('id_reporte') for r in rows])
        return Response({'ok': True, 'reportes': [_serialize_reporte(r, adjuntos) for r in rows]})

    def post(self, request):
        data = request.data
        tipo = (data.get('tipo_problema') or '').strip()
        descripcion = (data.get('descripcion') or '').strip()
        ubicacion = (data.get('ubicacion') or '').strip()
        zona = (data.get('zona') or '').strip()

        if tipo not in _tipo_meta():
            return Response({'ok': False, 'error': 'tipo_problema no válido'}, status=status.HTTP_400_BAD_REQUEST)
        if len(descripcion) < 10:
            return Response(
                {'ok': False, 'error': 'La descripción debe tener al menos 10 caracteres'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {
            'id_usuario': _user_id(request.user),
            'tipo_problema': tipo,
            'descripcion': descripcion,
            'ubicacion': f'[zona: {zona}] {ubicacion}'.strip() if zona else (ubicacion or None),
            'estado': 'pendiente',
            'prioridad': 'media',
        }
        try:
            row = supabase_client.insert('reporte', payload)
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        reporte_id = (row or {}).get('id_reporte')
        adjuntos = request.FILES.getlist('adjuntos')[:MAX_ADJUNTOS]
        guardados, errores = _guardar_adjuntos(reporte_id, adjuntos) if adjuntos else (0, [])
        if guardados:
            supabase_client.update(
                'reporte', {'foto': f'{guardados} adjunto(s)'}, {'id_reporte': f'eq.{reporte_id}'}
            )

        return Response({
            'ok': True,
            'reporte': _serialize_reporte(row or payload, _adjuntos_map([reporte_id])),
            'adjuntos_guardados': guardados,
            'adjuntos_errores': errores,
        }, status=status.HTTP_201_CREATED)


class MobileReporteDetalleView(APIView):
    """GET: detalle con adjuntos y mensajes. POST: enviar mensaje de chat."""
    permission_classes = [IsAuthenticatedUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_reporte(self, request, reporte_id):
        params = {'id_reporte': f'eq.{reporte_id}', 'limit': '1'}
        if not _es_admin(request.user):
            params['id_usuario'] = f'eq.{_user_id(request.user)}'
        rows = supabase_client.select('reporte', '*', params)
        return rows[0] if rows else None

    def get(self, request, reporte_id):
        try:
            reporte = self._get_reporte(request, reporte_id)
            if not reporte:
                return Response({'ok': False, 'error': 'no_encontrado'}, status=status.HTTP_404_NOT_FOUND)
            mensajes = supabase_client.select(
                'reporte_mensaje', '*',
                {'id_reporte': f'eq.{reporte_id}', 'order': 'id_mensaje.asc', 'limit': '500'},
            )
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        autores = _nombres_usuarios({reporte.get('id_usuario')} | {m.get('id_usuario') for m in mensajes})
        lector = _user_id(request.user)
        chat = [
            {
                'id_mensaje': m.get('id_mensaje'),
                'mensaje': m.get('mensaje'),
                'fecha_envio': m.get('fecha_envio'),
                'propio': m.get('id_usuario') == lector,
                'es_admin_emisor': (autores.get(m.get('id_usuario')) or {}).get('es_admin', False),
                'autor_nombre': (autores.get(m.get('id_usuario')) or {}).get('nombre', 'Usuario'),
            }
            for m in mensajes
        ]
        # Marcar como leídos los mensajes ajenos
        no_leidos = [m['id_mensaje'] for m in mensajes if m.get('id_usuario') != lector and not m.get('leido')]
        if no_leidos:
            supabase_client.update(
                'reporte_mensaje', {'leido': True},
                {'id_mensaje': f"in.({','.join(str(i) for i in no_leidos)})"},
            )
        return Response({
            'ok': True,
            'reporte': _serialize_reporte(reporte, _adjuntos_map([reporte_id]), autores),
            'mensajes': chat,
        })

    def post(self, request, reporte_id):
        mensaje = (request.data.get('mensaje') or '').strip()
        if not mensaje:
            return Response({'ok': False, 'error': 'mensaje vacío'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            reporte = self._get_reporte(request, reporte_id)
            if not reporte:
                return Response({'ok': False, 'error': 'no_encontrado'}, status=status.HTTP_404_NOT_FOUND)
            row = supabase_client.insert('reporte_mensaje', {
                'id_reporte': reporte_id,
                'id_usuario': _user_id(request.user),
                'mensaje': mensaje,
            })
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'ok': True, 'mensaje': row}, status=status.HTTP_201_CREATED)


class MobileReporteAdjuntarView(APIView):
    """POST multipart: agregar evidencias a un reporte propio."""
    permission_classes = [IsAuthenticatedUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, reporte_id):
        try:
            params = {'id_reporte': f'eq.{reporte_id}', 'id_usuario': f'eq.{_user_id(request.user)}', 'limit': '1'}
            rows = supabase_client.select('reporte', 'id_reporte', params)
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        if not rows:
            return Response({'ok': False, 'error': 'no_encontrado'}, status=status.HTTP_404_NOT_FOUND)

        archivos = request.FILES.getlist('adjuntos')[:MAX_ADJUNTOS]
        if not archivos:
            return Response({'ok': False, 'error': 'sin archivos'}, status=status.HTTP_400_BAD_REQUEST)
        guardados, errores = _guardar_adjuntos(reporte_id, archivos)
        return Response({'ok': True, 'adjuntos_guardados': guardados, 'adjuntos_errores': errores})


class MobileReportesComunidadView(APIView):
    """GET: feed comunitario con evidencias."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        try:
            rows = supabase_client.select(
                'reporte', '*', {'order': 'fecha_reporte.desc', 'limit': '100'}
            )
        except supabase_client.SupabaseError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        adjuntos = _adjuntos_map([r.get('id_reporte') for r in rows])
        autores = _nombres_usuarios({r.get('id_usuario') for r in rows})
        nombres = {uid: info['nombre'] for uid, info in autores.items()}
        return Response({'ok': True, 'reportes': [_serialize_reporte(r, adjuntos, nombres) for r in rows]})


class MobileReportesCatalogoView(APIView):
    """GET: tipos de problema disponibles para armar el formulario."""
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response({
            'ok': True,
            'tipos': [
                {'key': key, 'label': meta['label'], 'descripcion': meta['desc']}
                for key, meta in _tipo_meta().items()
            ],
            'max_adjuntos': MAX_ADJUNTOS,
        })
