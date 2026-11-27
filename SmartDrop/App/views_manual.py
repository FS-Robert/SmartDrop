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
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

import markdown
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.html import strip_tags
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


# ── Buscador dentro del manual (PB045) ──────────────────────────────────────
# Funciona en el servidor (sin JavaScript) para que sirva igual en la web y dentro de la app.

CONTENIDO_USUARIO = 'App/_manual_usuario_contenido.html'
CONTENIDO_ADMIN = 'App/_manual_admin_contenido.html'
_SECCION = re.compile(r'<section id="([^"]+)"[^>]*>(.*?)</section>', re.S)
_TITULO = re.compile(r'<h2[^>]*>(.*?)</h2>', re.S)
# Palabras que no ayudan a encontrar nada ("cómo reportar una fuga" busca "reportar" y "fuga").
_VACIAS = {
    'como', 'que', 'cual', 'donde', 'cuando', 'para', 'por', 'con', 'sin', 'del', 'los', 'las', 'una', 'uno',
    'unos', 'unas', 'mis', 'tus', 'sus', 'mio', 'puedo', 'hago', 'hacer', 'quiero', 'esta', 'este', 'esto',
    'hay', 'ver', 'son', 'muy', 'mas', 'pero', 'tengo', 'necesito', 'smartdrop',
}
MAX_BUSQUEDA = 80


def _sin_acentos(texto):
    texto = unicodedata.normalize('NFKD', texto.casefold())
    return ''.join(c for c in texto if not unicodedata.combining(c))


def _raiz(palabra):
    """'reportar' -> 'repor' (así encuentra reporte, reportes, reporta); 'fuga' -> 'fuga' (fugas)."""
    return palabra[:5] if len(palabra) > 5 else palabra


def _secciones(plantilla):
    """[(id, título, texto)] de cada sección del manual."""
    html = render_to_string(plantilla, {'en_app': True})
    secciones = []
    for id_seccion, cuerpo in _SECCION.findall(html):
        titulo = _TITULO.search(cuerpo)
        titulo = strip_tags(titulo.group(1)).strip() if titulo else id_seccion
        # Sin el título ni el pie de las capturas: el extracto sale del texto del manual.
        cuerpo = re.sub(r'<h2[^>]*>.*?</h2>|<figure.*?</figure>', ' ', cuerpo, flags=re.S)
        texto = re.sub(r'\s+', ' ', strip_tags(cuerpo)).strip()
        secciones.append((id_seccion, titulo, texto))
    return secciones


def _extracto(texto, raices):
    """La primera frase de la sección que tiene alguna de las palabras buscadas."""
    for frase in re.split(r'(?<=[.!?])\s+', texto):
        palabras = re.findall(r'\w+', _sin_acentos(frase))
        if len(frase) >= 25 and any(p.startswith(r) for p in palabras for r in raices):
            return frase if len(frase) <= 180 else frase[:177].rsplit(' ', 1)[0] + '…'
    return texto[:177] + '…'


def buscar_en_manual(consulta, plantilla):
    """Secciones del manual que tratan lo que se busca, de la más a la menos parecida (máximo 5)."""
    palabras = [p for p in re.findall(r'\w+', _sin_acentos(consulta or '')) if len(p) > 2 and p not in _VACIAS][:8]
    if not palabras:
        return []
    raices = [_raiz(p) for p in palabras]
    encontradas = []
    for id_seccion, titulo, texto in _secciones(plantilla):
        en_titulo = re.findall(r'\w+', _sin_acentos(titulo))
        en_texto = re.findall(r'\w+', _sin_acentos(texto))
        aciertos = [r for r in raices if any(t.startswith(r) for t in en_texto)]
        if not aciertos:
            continue
        veces = sum(1 for t in en_texto for r in aciertos if t.startswith(r))
        de_titulo = sum(1 for r in aciertos if any(t.startswith(r) for t in en_titulo))
        encontradas.append((len(aciertos), de_titulo, veces, id_seccion, titulo, _extracto(texto, raices)))
    if not encontradas:
        return []
    mejor = max(e[0] for e in encontradas)
    # Con varias palabras, solo las secciones que tienen la mayor cantidad de ellas.
    encontradas = [e for e in encontradas if e[0] == mejor]
    encontradas.sort(key=lambda e: (e[1], e[2]), reverse=True)
    return [{'id': e[3], 'titulo': e[4], 'extracto': e[5]} for e in encontradas[:5]]


def _contexto_busqueda(request, plantilla, mantener=()):
    """Datos del buscador para la plantilla. `mantener` son los parámetros que la página necesita (app, tipo, tema)."""
    consulta = (request.GET.get('q') or '').strip()[:MAX_BUSQUEDA]
    ocultos = [(clave, request.GET[clave]) for clave in mantener if request.GET.get(clave)]
    return {
        'q': consulta,
        'resultados': buscar_en_manual(consulta, plantilla) if consulta else [],
        'ocultos': ocultos,
        'sin_busqueda': urlencode(ocultos),
    }


@require_GET
def manual_usuario(request):
    if request.GET.get('app') == '1':
        contexto = {'oscuro': request.GET.get('tema') == 'oscuro', 'es_admin': False}
        if request.GET.get('tipo') == 'admin':
            # El manual del administrador solo se muestra con el token de un administrador.
            if not _admin_de_la_app(request):
                return HttpResponseForbidden('Solo para administradores.')
            contexto['es_admin'] = True
        plantilla = CONTENIDO_ADMIN if contexto['es_admin'] else CONTENIDO_USUARIO
        contexto.update(_contexto_busqueda(request, plantilla, mantener=('app', 'tipo', 'tema')))
        return render(request, 'App/manual_usuario_app.html', contexto)
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render(request, 'App/manual_usuario.html', _contexto_busqueda(request, CONTENIDO_USUARIO))


@login_required(login_url='login')
@require_GET
def manual_admin(request):
    """Manual del administrador: todo lo que hace el admin, con las electroválvulas paso a paso."""
    if not is_admin(request.user):
        return redirect('manual_usuario')
    return render(request, 'App/manual_admin.html', _contexto_busqueda(request, CONTENIDO_ADMIN))


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
