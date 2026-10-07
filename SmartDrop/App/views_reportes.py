"""Sistema de reportes de usuarios, reportes comunitarios con evidencia
fotográfica/video, historial de alertas y exportación CSV.

Los datos viven en Supabase (tablas reporte, reporte_adjunto,
reporte_mensaje, alerta, prediccion_desabasto). Los archivos adjuntos se
guardan en MEDIA_ROOT y su URL pública se registra en reporte_adjunto.
"""
import csv
import logging
import os
import uuid
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import preferencias, supabase_client
from .queries import is_admin, owner_id, run_parallel, users_by_id as _usuarios_por_id

logger = logging.getLogger(__name__)

# ── Catálogos ────────────────────────────────────────────────────────────────

TIPOS_PROBLEMA = [
    ('sin_agua', 'No llega agua', 'Interrupción total del suministro en tu vivienda o zona.', 'ti-droplet'),
    ('mala_calidad', 'Agua de mala calidad', 'Color, olor o sabor extraño; sedimentos o turbidez.', 'ti-test-pipe'),
    ('baja_presion', 'Baja presión', 'El agua llega con muy poca fuerza o de forma intermitente.', 'ti-gauge'),
    ('fuga', 'Fuga visibles', 'Fugas en la red pública, medidor o tuberías exteriores.', 'ti-alert-triangle'),
    ('tanque', 'Problema con el tanque', 'Nivel incorrecto, sensor sin datos o desbordamiento.', 'ti-building-warehouse'),
    ('facturacion', 'Facturación / pagos', 'Cobros incorrectos o dudas sobre tu estado de pago.', 'ti-file-invoice'),
    ('otro', 'Otro problema', 'Cualquier otro inconveniente relacionado con el servicio.', 'ti-dots'),
]

ESTADOS_REPORTE = ['pendiente', 'en_proceso', 'resuelto']
ESTADO_LABEL = {'pendiente': 'Pendiente', 'en_proceso': 'En proceso', 'resuelto': 'Resuelto'}
PRIORIDADES = ['baja', 'media', 'alta']

MAX_ADJUNTOS = 5
MAX_TAMANO_ARCHIVO = 50 * 1024 * 1024  # 50 MB
EXT_IMAGEN = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
EXT_VIDEO = {'.mp4', '.webm', '.mov', '.3gp'}


def _tipo_meta():
    return {key: {'label': label, 'desc': desc, 'icon': icon} for key, label, desc, icon in TIPOS_PROBLEMA}


def _tipo_label(key):
    return _tipo_meta().get(key, {}).get('label', key or 'Otro')


def _es_admin(user):
    return is_admin(user)


def _alertas_visibles(alertas, es_admin, user=None, prefs=None):
    """Alertas que el usuario puede ver.

    Los avisos de fuga del motor de predicción llevan nombre, teléfono y dirección del titular: solo los
    ve el administrador. Los reportes semanales son personales, y con `user` se ocultan además las
    categorías de notificación que desactivó en su perfil (`prefs` evita releerlas de la base).
    """
    if not es_admin:
        alertas = [a for a in alertas if (a.get('datos_adicionales') or {}).get('origen') != 'ml_engine']
    if user is not None:
        user_id = owner_id(user)
        alertas = preferencias.filtrar_alertas(
            [a for a in alertas if not preferencias.es_reporte_ajeno(a, user_id)],
            prefs or preferencias.preferencias_de(user),
        )
    return alertas


def _supabase_id(request):
    return owner_id(request.user)


def _adjuntos_por_reporte(reporte_ids):
    """Devuelve {id_reporte: [adjuntos]} con una sola consulta IN."""
    if not reporte_ids:
        return {}
    ids = ','.join(str(i) for i in reporte_ids)
    try:
        rows = supabase_client.select(
            'reporte_adjunto', '*',
            {'id_reporte': f'in.({ids})', 'order': 'id_adjunto.asc', 'limit': '500'},
        )
    except supabase_client.SupabaseError:
        rows = []
    agrupados = {}
    for row in rows:
        agrupados.setdefault(row.get('id_reporte'), []).append(row)
    return agrupados


def _contadores_chat(reporte_ids, es_admin, user_id):
    """Mensajes sin leer dirigidos al usuario actual, agrupados por reporte."""
    if not reporte_ids:
        return {}
    ids = ','.join(str(i) for i in reporte_ids)
    try:
        rows = supabase_client.select(
            'reporte_mensaje', 'id_reporte,id_usuario,leido',
            {'id_reporte': f'in.({ids})', 'leido': 'eq.false', 'limit': '2000'},
        )
    except supabase_client.SupabaseError:
        return {}
    contadores = {}
    for row in rows:
        # Cuentan los mensajes aún no leídos que no envió el lector actual.
        if row.get('id_usuario') != user_id:
            contadores[row['id_reporte']] = contadores.get(row['id_reporte'], 0) + 1
    return contadores


def _guardar_adjuntos(reporte_id, archivos):
    """Guarda archivos en MEDIA_ROOT/reportes/<id>/ y registra cada URL en Supabase."""
    guardados, errores = 0, []
    for archivo in archivos[:MAX_ADJUNTOS]:
        nombre = archivo.name or 'adjunto'
        ext = os.path.splitext(nombre)[1].lower()
        if ext not in EXT_IMAGEN | EXT_VIDEO:
            errores.append(f'{nombre}: formato no permitido.')
            continue
        if archivo.size > MAX_TAMANO_ARCHIVO:
            errores.append(f'{nombre}: supera los 50 MB.')
            continue

        tipo = 'imagen' if ext in EXT_IMAGEN else 'video'
        destino_rel = os.path.join('reportes', str(reporte_id), f'{uuid.uuid4().hex}{ext}')
        destino_abs = os.path.join(settings.MEDIA_ROOT, destino_rel)
        os.makedirs(os.path.dirname(destino_abs), exist_ok=True)
        with open(destino_abs, 'wb+') as out:
            for chunk in archivo.chunks():
                out.write(chunk)

        url = f'{settings.MEDIA_URL}{destino_rel.replace(os.sep, "/")}'
        try:
            supabase_client.insert('reporte_adjunto', {
                'id_reporte': reporte_id,
                'url': url,
                'tipo': tipo,
                'nombre': nombre,
            })
            guardados += 1
        except supabase_client.SupabaseError:
            logger.exception('No se pudo registrar adjunto %s del reporte %s', nombre, reporte_id)
            errores.append(f'{nombre}: no se pudo registrar.')
    return guardados, errores


def _crear_reporte(request, es_comunitario):
    """Crea un reporte desde el formulario web. Devuelve (ok, mensaje)."""
    tipo = (request.POST.get('tipo_problema') or '').strip()
    descripcion = (request.POST.get('descripcion') or '').strip()
    ubicacion = (request.POST.get('ubicacion') or '').strip()
    zona = (request.POST.get('zona') or '').strip()

    if tipo not in _tipo_meta():
        return False, 'Selecciona un tipo de problema válido.'
    if len(descripcion) < 10:
        return False, 'Describe el problema con al menos 10 caracteres.'

    payload = {
        'id_usuario': _supabase_id(request),
        'tipo_problema': tipo,
        'descripcion': descripcion,
        'ubicacion': ubicacion or None,
        'estado': 'pendiente',
        'prioridad': 'media',
    }
    if es_comunitario and zona:
        payload['ubicacion'] = f'[zona: {zona}] {ubicacion}'.strip()

    try:
        row = supabase_client.insert('reporte', payload)
    except supabase_client.SupabaseError as exc:
        logger.exception('Error creando reporte')
        return False, f'No se pudo enviar el reporte: {exc}'

    reporte_id = (row or {}).get('id_reporte')
    archivos = request.FILES.getlist('adjuntos')
    if reporte_id and archivos:
        guardados, errores = _guardar_adjuntos(reporte_id, archivos)
        if errores:
            messages.warning(request, 'Algunos adjuntos no se subieron: ' + ' '.join(errores))
        if guardados:
            # compatibilidad con la columna legado `foto`
            supabase_client.update(
                'reporte',
                {'foto': f'{guardados} adjunto(s)'},
                {'id_reporte': f'eq.{reporte_id}'},
            )
    return True, 'Reporte enviado. El equipo de SmartDrop lo revisará pronto.'


# ── Vistas de usuario ─────────────────────────────────────────────────────────

@login_required(login_url='login')
def reportes_panel(request):
    """Panel del usuario: problemas comunes, formulario de reporte e historial."""
    if _es_admin(request.user):
        return redirect('admin_reportes')

    if request.method == 'POST':
        ok, mensaje = _crear_reporte(request, es_comunitario=False)
        (messages.success if ok else messages.error)(request, mensaje)
        if ok:
            return redirect('reportes_panel')

    user_id = _supabase_id(request)
    try:
        mis_reportes = supabase_client.select(
            'reporte', '*',
            {'id_usuario': f'eq.{user_id}', 'order': 'fecha_reporte.desc', 'limit': '100'},
        )
    except supabase_client.SupabaseError:
        mis_reportes = []
        messages.error(request, 'No se pudo cargar tu historial de reportes.')

    for reporte in mis_reportes:
        reporte['tipo_label'] = _tipo_label(reporte.get('tipo_problema'))
        reporte['estado_label'] = ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado'))

    adjuntos = _adjuntos_por_reporte([r.get('id_reporte') for r in mis_reportes])
    for reporte in mis_reportes:
        reporte['n_adjuntos'] = len(adjuntos.get(reporte.get('id_reporte'), []))

    return render(request, 'App/reportes.html', {
        'tipos_problema': _tipo_meta(),
        'mis_reportes': mis_reportes,
        'max_adjuntos': MAX_ADJUNTOS,
    })


def _cargar_reporte_o_404(request, reporte_id, solo_propietario=True):
    params = {'id_reporte': f'eq.{reporte_id}', 'limit': '1'}
    if solo_propietario:
        params['id_usuario'] = f'eq.{_supabase_id(request)}'
    rows = supabase_client.select('reporte', '*', params)
    return rows[0] if rows else None


def _hay_sin_leer(mensajes, lector_id):
    return any(m.get('id_usuario') != lector_id and not m.get('leido') for m in mensajes)


def _marcar_leidos(reporte_id, lector_id):
    """Marca como leídos los mensajes del reporte que NO envió el lector (una sola actualización)."""
    try:
        supabase_client.update(
            'reporte_mensaje', {'leido': True},
            {
                'id_reporte': f'eq.{reporte_id}', 'leido': 'eq.false',
                'or': f'(id_usuario.neq.{lector_id},id_usuario.is.null)',
            },
        )
    except supabase_client.SupabaseError:
        logger.warning('No se pudieron marcar como leídos los mensajes del reporte %s', reporte_id)


@login_required(login_url='login')
def reporte_detalle(request, reporte_id):
    """Detalle del reporte: evidencias y chat usuario↔admin."""
    es_admin = _es_admin(request.user)
    try:
        reporte = _cargar_reporte_o_404(request, reporte_id, solo_propietario=not es_admin)
    except supabase_client.SupabaseError:
        reporte = None
    if not reporte:
        messages.error(request, 'Reporte no encontrado.')
        return redirect('admin_reportes' if es_admin else 'reportes_panel')

    if request.method == 'POST':
        mensaje = (request.POST.get('mensaje') or '').strip()
        es_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        if mensaje:
            try:
                row = supabase_client.insert('reporte_mensaje', {
                    'id_reporte': reporte_id,
                    'id_usuario': _supabase_id(request),
                    'mensaje': mensaje,
                })
                if es_ajax:
                    autor_nombre = request.user.get_full_name() or getattr(request.user, 'nombre', 'Usuario')
                    return JsonResponse({
                        'ok': True,
                        'mensaje': {
                            'id_mensaje': row.get('id_mensaje') if row else None,
                            'mensaje': mensaje,
                            'fecha_envio': (row or {}).get('fecha_envio'),
                            'propio': True,
                            'es_admin_emisor': es_admin,
                            'autor_nombre': autor_nombre,
                        },
                    })
            except supabase_client.SupabaseError as exc:
                if es_ajax:
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
                messages.error(request, f'No se pudo enviar el mensaje: {exc}')
        elif es_ajax:
            return JsonResponse({'ok': False, 'error': 'mensaje_vacio'}, status=400)
        return redirect('reporte_detalle', reporte_id=reporte_id)

    adjuntos, mensajes_rows = [], []
    try:
        adjuntos, mensajes_rows = run_parallel(
            lambda: supabase_client.select(
                'reporte_adjunto', '*',
                {'id_reporte': f'eq.{reporte_id}', 'order': 'id_adjunto.asc', 'limit': '100'},
            ),
            lambda: supabase_client.select(
                'reporte_mensaje', '*',
                {'id_reporte': f'eq.{reporte_id}', 'order': 'id_mensaje.asc', 'limit': '500'},
            ),
        )
    except supabase_client.SupabaseError:
        messages.error(request, 'No se pudo cargar la conversación.')

    # Nombres de los participantes (autor del reporte + admins que escriben)
    participantes = {reporte.get('id_usuario'), _supabase_id(request)}
    participantes.update(m.get('id_usuario') for m in mensajes_rows)
    nombres = _usuarios_por_id(participantes, 'id_usuario,nombre,apellido,id_rol')

    lector_id = _supabase_id(request)
    mensajes_chat = []
    for mensaje in mensajes_rows:
        emisor = nombres.get(mensaje.get('id_usuario'), {})
        mensajes_chat.append({
            **mensaje,
            'propio': mensaje.get('id_usuario') == lector_id,
            'es_admin_emisor': emisor.get('id_rol') == 2,
            'autor_nombre': f"{emisor.get('nombre', '')} {emisor.get('apellido', '')}".strip() or 'Usuario',
        })
    if _hay_sin_leer(mensajes_rows, lector_id):
        _marcar_leidos(reporte_id, lector_id)

    autor = nombres.get(reporte.get('id_usuario'), {})
    reporte['tipo_label'] = _tipo_label(reporte.get('tipo_problema'))
    reporte['estado_label'] = ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado'))
    reporte['autor_nombre'] = f"{autor.get('nombre', '')} {autor.get('apellido', '')}".strip() or 'Usuario'

    return render(request, 'App/reporte_detalle.html', {
        'reporte': reporte,
        'adjuntos': adjuntos,
        'mensajes_chat': mensajes_chat,
        'es_admin': es_admin,
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
def reporte_adjuntar(request, reporte_id):
    """El usuario agrega más evidencias a un reporte propio."""
    if _es_admin(request.user):
        return redirect('admin_reportes')
    try:
        reporte = _cargar_reporte_o_404(request, reporte_id, solo_propietario=True)
    except supabase_client.SupabaseError:
        reporte = None
    if not reporte:
        messages.error(request, 'Reporte no encontrado.')
        return redirect('reportes_panel')

    archivos = request.FILES.getlist('adjuntos')
    if not archivos:
        messages.warning(request, 'Selecciona al menos un archivo.')
    else:
        guardados, errores = _guardar_adjuntos(reporte_id, archivos)
        if guardados:
            messages.success(request, f'{guardados} evidencia(s) agregada(s).')
        for error in errores:
            messages.warning(request, error)
    return redirect('reporte_detalle', reporte_id=reporte_id)


# ── Reportes comunitarios ─────────────────────────────────────────────────────

@login_required(login_url='login')
def reportes_comunidad(request):
    """Muro comunitario: reportes de zona con evidencia fotográfica/video."""
    if request.method == 'POST':
        ok, mensaje = _crear_reporte(request, es_comunitario=True)
        (messages.success if ok else messages.error)(request, mensaje)
        if ok:
            return redirect('reportes_comunidad')

    try:
        reportes = supabase_client.select(
            'reporte', '*', {'order': 'fecha_reporte.desc', 'limit': '100'},
        )
    except supabase_client.SupabaseError:
        reportes = []
        messages.error(request, 'No se pudieron cargar los reportes de la comunidad.')

    # Adjuntos y autores no dependen entre sí: se piden a la vez.
    adjuntos_map, usuarios = run_parallel(
        lambda: _adjuntos_por_reporte([r.get('id_reporte') for r in reportes]),
        lambda: _usuarios_por_id((r.get('id_usuario') for r in reportes), 'id_usuario,nombre,apellido'),
    )
    autores = {
        user_id: f"{row.get('nombre', '')} {(row.get('apellido') or '')[:1]}.".strip()
        for user_id, row in usuarios.items()
    }

    feed = []
    for reporte in reportes:
        adjuntos = adjuntos_map.get(reporte.get('id_reporte'), [])
        if not adjuntos and not reporte.get('foto'):
            # El muro comunitario destaca reportes con evidencia; sin evidencia
            # igual se muestran, pero marcados.
            pass
        feed.append({
            **reporte,
            'tipo_label': _tipo_label(reporte.get('tipo_problema')),
            'estado_label': ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado')),
            'adjuntos': adjuntos,
            'tiene_evidencia': bool(adjuntos or reporte.get('foto')),
            'autor_nombre': autores.get(reporte.get('id_usuario'), 'Vecino'),
            'propio': reporte.get('id_usuario') == (request.user.supabase_id or request.user.id_usuario),
        })

    return render(request, 'App/reportes_comunidad.html', {
        'feed': feed,
        'tipos_problema': _tipo_meta(),
        'max_adjuntos': MAX_ADJUNTOS,
    })


# ── Panel de administración ──────────────────────────────────────────────────

@login_required(login_url='login')
def admin_reportes(request):
    """Panel admin: lista de problemas reportados con filtros y actualización."""
    if not _es_admin(request.user):
        return redirect('reportes_panel')

    filtro_estado = request.GET.get('estado', '')
    filtro_tipo = request.GET.get('tipo', '')
    params = {'order': 'fecha_reporte.desc', 'limit': '500'}
    if filtro_estado in ESTADOS_REPORTE:
        params['estado'] = f'eq.{filtro_estado}'
    if filtro_tipo in _tipo_meta():
        params['tipo_problema'] = f'eq.{filtro_tipo}'

    try:
        reportes = supabase_client.select('reporte', '*', params)
    except supabase_client.SupabaseError:
        reportes = []
        messages.error(request, 'No se pudieron cargar los reportes.')

    reporte_ids = [r.get('id_reporte') for r in reportes]
    admin_id = _supabase_id(request)
    # Autores, adjuntos y mensajes sin leer son consultas independientes: se piden a la vez.
    autores, adjuntos_map, sin_leer = run_parallel(
        lambda: _usuarios_por_id((r.get('id_usuario') for r in reportes), 'id_usuario,nombre,apellido,correo'),
        lambda: _adjuntos_por_reporte(reporte_ids),
        lambda: _contadores_chat(reporte_ids, True, admin_id),
    )

    lista = []
    for reporte in reportes:
        autor = autores.get(reporte.get('id_usuario'), {})
        lista.append({
            **reporte,
            'tipo_label': _tipo_label(reporte.get('tipo_problema')),
            'estado_label': ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado')),
            'autor_nombre': f"{autor.get('nombre', '')} {autor.get('apellido', '')}".strip() or 'Usuario',
            'autor_correo': autor.get('correo', ''),
            'n_adjuntos': len(adjuntos_map.get(reporte.get('id_reporte'), [])),
            'sin_leer': sin_leer.get(reporte.get('id_reporte'), 0),
        })

    conteos = {estado: 0 for estado in ESTADOS_REPORTE}
    for reporte in lista:
        if reporte.get('estado') in conteos:
            conteos[reporte['estado']] += 1

    return render(request, 'App/admin_reportes.html', {
        'reportes': lista,
        'conteos': conteos,
        'filtro_estado': filtro_estado,
        'filtro_tipo': filtro_tipo,
        'tipos_problema': _tipo_meta(),
        'estados': ESTADOS_REPORTE,
        'prioridades': PRIORIDADES,
    })


@login_required(login_url='login')
@require_http_methods(['POST'])
def admin_reporte_update(request, reporte_id):
    """Actualiza estado, prioridad, asignación y respuesta del admin."""
    if not _es_admin(request.user):
        return redirect('reportes_panel')

    estado = request.POST.get('estado', '')
    prioridad = request.POST.get('prioridad', '')
    respuesta = (request.POST.get('respuesta_admin') or '').strip()

    cambios = {}
    if estado in ESTADOS_REPORTE:
        cambios['estado'] = estado
    if prioridad in PRIORIDADES:
        cambios['prioridad'] = prioridad
    if respuesta:
        cambios['respuesta_admin'] = respuesta
        cambios['id_usuario_atiende'] = _supabase_id(request)
        from django.utils import timezone
        cambios['fecha_atencion'] = timezone.now().isoformat()

    if cambios:
        try:
            supabase_client.update('reporte', cambios, {'id_reporte': f'eq.{reporte_id}'})
            messages.success(request, 'Reporte actualizado.')
            # La respuesta también aparece como mensaje del chat
            if respuesta:
                supabase_client.insert('reporte_mensaje', {
                    'id_reporte': reporte_id,
                    'id_usuario': _supabase_id(request),
                    'mensaje': f'Respuesta del equipo: {respuesta}',
                })
        except supabase_client.SupabaseError as exc:
            messages.error(request, f'No se pudo actualizar: {exc}')

    return redirect('reporte_detalle', reporte_id=reporte_id)


@login_required(login_url='login')
def admin_reportes_export(request):
    """Exporta los reportes (con filtros aplicados) como CSV."""
    if not _es_admin(request.user):
        return redirect('reportes_panel')

    params = {'order': 'fecha_reporte.desc', 'limit': '5000'}
    if request.GET.get('estado') in ESTADOS_REPORTE:
        params['estado'] = f"eq.{request.GET['estado']}"
    if request.GET.get('tipo') in _tipo_meta():
        params['tipo_problema'] = f"eq.{request.GET['tipo']}"

    try:
        reportes = supabase_client.select('reporte', '*', params)
    except supabase_client.SupabaseError:
        reportes = []

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="reportes_smartdrop.csv"'
    response.write('﻿')  # BOM para Excel
    writer = csv.writer(response)
    writer.writerow([
        'ID', 'Fecha', 'Tipo', 'Descripción', 'Ubicación',
        'Estado', 'Prioridad', 'ID usuario', 'Respuesta admin', 'Fecha atención',
    ])
    for reporte in reportes:
        writer.writerow([
            reporte.get('id_reporte'),
            reporte.get('fecha_reporte'),
            _tipo_label(reporte.get('tipo_problema')),
            (reporte.get('descripcion') or '').replace('\n', ' '),
            reporte.get('ubicacion') or '',
            ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado')),
            reporte.get('prioridad') or '',
            reporte.get('id_usuario') or '',
            (reporte.get('respuesta_admin') or '').replace('\n', ' '),
            reporte.get('fecha_atencion') or '',
        ])
    return response


# ── Historial de alertas ──────────────────────────────────────────────────────

_TIPOS_ALERTA = {
    'fuga': 'ti-alert-triangle',
    'calidad': 'ti-test-pipe',
    'desabasto': 'ti-droplet',
    'nivel': 'ti-building-warehouse',
    'presion': 'ti-gauge',
    'valvula': 'ti-toggle-left',
    'sensor': 'ti-devices',
}


def _alerta_icon(tipo):
    tipo = (tipo or '').lower()
    for clave, icon in _TIPOS_ALERTA.items():
        if clave in tipo:
            return icon
    return 'ti-bell'


_TITULOS_ALERTA = {
    'fuga': 'Posible fuga', 'consumo_elevado': 'Consumo elevado', 'presion_baja': 'Presión baja',
    'nivel_tanque_bajo': 'Nivel de tanque bajo', 'calidad_agua': 'Calidad del agua',
    'desabasto': 'Riesgo de desabasto',
}
_ESTADOS_ALERTA = {
    'pendiente': 'Pendiente', 'no_confirmada': 'Pendiente', 'confirmada': 'Confirmada',
    'descartada': 'Descartada', 'resuelta': 'Resuelta',
}


def _alerta_legible(alerta):
    """Título, resumen corto y detalle opcional para mostrar la alerta sin un bloque largo de texto."""
    tipo = (alerta.get('tipo_alerta') or '').lower()
    alerta['titulo'] = _TITULOS_ALERTA.get(tipo) or tipo.replace('_', ' ').capitalize() or 'Alerta'
    alerta['estado_texto'] = _ESTADOS_ALERTA.get(alerta.get('estado_confirmacion'), (alerta.get('estado_confirmacion') or '').title())
    lineas = [linea.strip() for linea in (alerta.get('mensaje') or '').splitlines() if linea.strip()]
    alerta['resumen'] = lineas[0] if lineas else ''
    alerta['detalle'] = lineas[1:]
    fecha = alerta.get('fecha_creacion') or ''
    try:
        alerta['fecha_texto'] = timezone.localtime(datetime.fromisoformat(fecha.replace('Z', '+00:00'))).strftime('%d/%m/%Y %H:%M')
    except ValueError:
        alerta['fecha_texto'] = fecha[:16].replace('T', ' ')
    return alerta


@login_required(login_url='login')
def alertas_historial(request):
    """Historial de alertas del sistema con filtros y exportación CSV."""
    es_admin = _es_admin(request.user)
    filtro_estado = request.GET.get('estado', '')
    params = {'order': 'fecha_creacion.desc', 'limit': '500'}
    if filtro_estado:
        params['estado_confirmacion'] = f'eq.{filtro_estado}'

    try:
        alertas = supabase_client.select('alerta', '*', params)
    except supabase_client.SupabaseError:
        alertas = []
        messages.error(request, 'No se pudo cargar el historial de alertas.')

    alertas = _alertas_visibles(alertas, es_admin, request.user)
    for alerta in alertas:
        alerta['icon'] = _alerta_icon(alerta.get('tipo_alerta'))
        _alerta_legible(alerta)

    return render(request, 'App/alertas.html', {
        'alertas': alertas,
        'filtro_estado': filtro_estado,
        'es_admin': es_admin,
    })


@login_required(login_url='login')
def alertas_export(request):
    """Exporta el historial de alertas como CSV (solo administradores)."""
    if not _es_admin(request.user):
        return redirect('alertas_historial')

    params = {'order': 'fecha_creacion.desc', 'limit': '5000'}
    if request.GET.get('estado'):
        params['estado_confirmacion'] = f"eq.{request.GET['estado']}"
    try:
        alertas = supabase_client.select('alerta', '*', params)
    except supabase_client.SupabaseError:
        alertas = []

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="alertas_smartdrop.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['ID', 'Fecha', 'Tipo', 'Prioridad', 'Mensaje', 'Estado'])
    for alerta in alertas:
        writer.writerow([
            alerta.get('id_alerta'),
            alerta.get('fecha_creacion'),
            alerta.get('tipo_alerta'),
            alerta.get('prioridad'),
            (alerta.get('mensaje') or '').replace('\n', ' '),
            alerta.get('estado_confirmacion'),
        ])
    return response


# ── Dashboard: interrupciones y predicciones ──────────────────────────────────

def interrupciones_y_predicciones(user, prefs=None):
    """Datos para el bloque del dashboard: reportes abiertos de suministro,
    alertas recientes y predicciones de desabasto. Nunca lanza excepción."""
    resultado = {'reportes_abiertos': [], 'alertas': [], 'predicciones': []}
    user_id = owner_id(user)
    es_admin = is_admin(user)

    try:
        if es_admin:
            params_reportes = {'estado': 'neq.resuelto', 'order': 'fecha_reporte.desc', 'limit': '5'}
        else:
            params_reportes = {
                'id_usuario': f'eq.{user_id}', 'estado': 'neq.resuelto',
                'order': 'fecha_reporte.desc', 'limit': '5',
            }
        reportes, alertas, predicciones = [], [], []
        try:
            reportes, alertas, predicciones = run_parallel(
                lambda: supabase_client.select('reporte', '*', params_reportes),
                lambda: supabase_client.select(
                    'alerta', 'id_alerta,tipo_alerta,prioridad,mensaje,estado_confirmacion,fecha_creacion,datos_adicionales',
                    {'order': 'fecha_creacion.desc', 'limit': '40'},
                ),
                lambda: supabase_client.select(
                    'prediccion_desabasto',
                    'id_prediccion,id_tanque,fecha_prediccion,nivel_riesgo,horas_autonomia_estimadas,porcentaje_llenado,estado',
                    {'order': 'fecha_prediccion.desc', 'limit': '3'},
                ),
            )
        except supabase_client.SupabaseError:
            pass

        for reporte in reportes:
            reporte['tipo_label'] = _tipo_label(reporte.get('tipo_problema'))
            reporte['estado_label'] = ESTADO_LABEL.get(reporte.get('estado'), reporte.get('estado'))
        alertas = _alertas_visibles(alertas, es_admin, user, prefs)[:5]
        for alerta in alertas:
            alerta['icon'] = _alerta_icon(alerta.get('tipo_alerta'))

        resultado['reportes_abiertos'] = reportes
        resultado['alertas'] = alertas
        resultado['predicciones'] = predicciones
    except Exception:
        logger.exception('No se pudo cargar el bloque de interrupciones/predicciones')
    return resultado


@login_required(login_url='login')
@require_http_methods(['GET'])
def reporte_mensajes_json(request, reporte_id):
    """Polling ligero del chat: mensajes del reporte en JSON."""
    es_admin = _es_admin(request.user)
    try:
        reporte = _cargar_reporte_o_404(request, reporte_id, solo_propietario=not es_admin)
        if not reporte:
            return JsonResponse({'ok': False, 'error': 'no_encontrado'}, status=404)
        after = request.GET.get('after', '0')
        params = {'id_reporte': f'eq.{reporte_id}', 'order': 'id_mensaje.asc', 'limit': '200'}
        if after.isdigit() and int(after) > 0:
            params['id_mensaje'] = f'gt.{after}'
        filas = supabase_client.select('reporte_mensaje', '*', params)
    except supabase_client.SupabaseError:
        return JsonResponse({'ok': False, 'error': 'db_error'}, status=502)

    lector_id = _supabase_id(request)
    nombres = _usuarios_por_id((m.get('id_usuario') for m in filas), 'id_usuario,nombre,apellido,id_rol')

    mensajes_chat = []
    for m in filas:
        emisor = nombres.get(m.get('id_usuario'), {})
        mensajes_chat.append({
            'id_mensaje': m.get('id_mensaje'),
            'mensaje': m.get('mensaje'),
            'fecha_envio': m.get('fecha_envio'),
            'propio': m.get('id_usuario') == lector_id,
            'es_admin_emisor': emisor.get('id_rol') == 2,
            'autor_nombre': f"{emisor.get('nombre', '')} {emisor.get('apellido', '')}".strip() or 'Usuario',
        })
    if _hay_sin_leer(filas, lector_id):
        _marcar_leidos(reporte_id, lector_id)
    return JsonResponse({'ok': True, 'mensajes': mensajes_chat})
