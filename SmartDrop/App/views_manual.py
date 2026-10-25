"""Manuales.

* Manual de usuario (lo que ve un usuario común): /manual/ en la web y /manual/?app=1 dentro de la app.
  El contenido está en App/Templates/App/_manual_usuario_contenido.html. La versión de la app no pide sesión:
  la app la abre en una pantalla propia, sin las cookies de la web, y el manual no tiene datos de nadie.
* Manual del administrador (todo lo del admin, con las electroválvulas): /manual-admin/ en la web y
  /manual/?app=1&tipo=admin en la app, que manda su token (solo se muestra a administradores).
  El contenido está en App/Templates/App/_manual_admin_contenido.html.
* Manual técnico: /manual-tecnico/, solo para administradores. Muestra docs/MANUAL_TECNICO.md del repositorio
  (así hay un solo manual que mantener) y permite bajar la colección de Postman.
"""
import re
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET
from rest_framework.exceptions import AuthenticationFailed

from .api.authentication import SupabaseJWTAuthentication
from .queries import is_admin

CARPETA_DOCS = Path(settings.BASE_DIR).parent / 'docs'
MANUAL_TECNICO = CARPETA_DOCS / 'MANUAL_TECNICO.md'
POSTMAN = CARPETA_DOCS / 'SmartDrop.postman_collection.json'

# Los diagramas vienen en bloques ```mermaid; se dejan como <pre class="mermaid"> para que el navegador los dibuje.
_MERMAID = re.compile(r'<pre><code class="language-mermaid">(.*?)</code></pre>', re.S)
_cache = {'fecha': None, 'html': '', 'indice': ''}


def _admin_de_la_app(request):
    """True si la petición trae el token de la app de un administrador (la app no tiene la sesión de la web)."""
    try:
        resultado = SupabaseJWTAuthentication().authenticate(request)
    except AuthenticationFailed:
        return False
    return bool(resultado) and is_admin(resultado[0])


@require_GET
def manual_usuario(request):
    if request.GET.get('app') == '1':
        contexto = {'oscuro': request.GET.get('tema') == 'oscuro', 'es_admin': False}
        if request.GET.get('tipo') == 'admin':
            # El manual del administrador solo se muestra con el token de un administrador.
            if not _admin_de_la_app(request):
                return HttpResponseForbidden('Solo para administradores.')
            contexto['es_admin'] = True
        return render(request, 'App/manual_usuario_app.html', contexto)
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render(request, 'App/manual_usuario.html')


@login_required(login_url='login')
@require_GET
def manual_admin(request):
    """Manual del administrador: todo lo que hace el admin, con las electroválvulas paso a paso."""
    if not is_admin(request.user):
        return redirect('manual_usuario')
    return render(request, 'App/manual_admin.html')


def _manual_tecnico_html():
    """(contenido, índice) del manual técnico en HTML. Se vuelve a convertir solo si el archivo cambió."""
    fecha = MANUAL_TECNICO.stat().st_mtime
    if _cache['fecha'] != fecha:
        convertidor = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc', 'sane_lists'],
                                        extension_configs={'toc': {'toc_depth': '2-3'}})
        contenido = convertidor.convert(MANUAL_TECNICO.read_text(encoding='utf-8'))
        contenido = _MERMAID.sub(lambda m: f'<pre class="mermaid">{m.group(1)}</pre>', contenido)
        # Los enlaces a archivos del repositorio no existen en la web: se dejan como texto.
        contenido = re.sub(r'<a href="(\.\./|SmartDrop\.postman)[^"]*">(.*?)</a>', r'<code>\2</code>', contenido)
        _cache.update(fecha=fecha, html=contenido, indice=convertidor.toc)
    return _cache['html'], _cache['indice']


@login_required(login_url='login')
@require_GET
def manual_tecnico(request):
    if not is_admin(request.user):
        return redirect('dashboard')
    try:
        contenido, indice = _manual_tecnico_html()
    except OSError:
        contenido, indice = '<p>No se encontró <code>docs/MANUAL_TECNICO.md</code> en el servidor.</p>', ''
    return render(request, 'App/manual_tecnico.html', {
        'contenido': mark_safe(contenido),   # viene de un archivo del propio repositorio, no de un usuario
        'indice': mark_safe(indice),
        'hay_postman': POSTMAN.exists(),
    })


@login_required(login_url='login')
@require_GET
def manual_tecnico_postman(request):
    if not is_admin(request.user):
        return redirect('dashboard')
    if not POSTMAN.exists():
        raise Http404('No está la colección de Postman.')
    return FileResponse(POSTMAN.open('rb'), as_attachment=True, filename=POSTMAN.name,
                        content_type='application/json')
