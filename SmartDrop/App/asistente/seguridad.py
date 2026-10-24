"""Cuidados de lo que entra y sale de Gotita.

Lo que entra: preguntas cortas, poca conversación anterior y fotos de pantalla pequeñas y de tipo imagen.
Lo que sale: cada frase se revisa antes de mostrarla o leerla. Se quitan los símbolos de formato, se tapan
claves y contraseñas, y nunca sale código aunque Claude lo escriba. Es un segundo candado: el primero son
las reglas que se le dan a Gotita.
"""
import base64
import binascii
import re

MAX_TEXTO = 600
MAX_TITULO = 80
MAX_PANTALLA = 12000
MAX_SECCIONES = 40
MAX_HISTORIAL = 8
MAX_HISTORIAL_TEXTO = 800
MAX_CAPTURA_BYTES = 1_500_000
MODOS = ('sencillo', 'tecnico')
IDIOMAS = ('es', 'en')

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_ID = re.compile(r'^[a-z0-9_\-]{1,40}$')
_DATA_URL = re.compile(r'^data:(image/(?:jpeg|png|webp));base64,(.+)$', re.DOTALL)
_FIRMAS = {'image/jpeg': (b'\xff\xd8\xff',), 'image/png': (b'\x89PNG',), 'image/webp': (b'RIFF',)}

AVISO_CODIGO = {
    'es': 'Eso no lo puedo mostrar: no comparto código ni comandos.',
    'en': "I can't show that: I don't share code or commands.",
}
OCULTO = '[dato oculto]'


class EntradaInvalida(ValueError):
    pass


def _texto(valor, maximo):
    if valor is None:
        return ''
    texto = _CONTROL.sub(' ', str(valor)).strip()
    return texto[:maximo]


def _captura(valor):
    """Revisa que la foto de la pantalla sea una imagen de verdad y no muy grande. Devuelve None si no vino."""
    if not valor:
        return None
    if isinstance(valor, dict):
        media_type, datos = str(valor.get('media_type') or ''), str(valor.get('data') or '')
    else:
        coincide = _DATA_URL.match(str(valor).strip())
        if not coincide:
            raise EntradaInvalida('La captura de pantalla no tiene un formato válido.')
        media_type, datos = coincide.group(1), coincide.group(2)
    if media_type not in _FIRMAS:
        raise EntradaInvalida('La captura de pantalla debe ser JPEG, PNG o WebP.')
    datos = re.sub(r'\s+', '', datos)
    if len(datos) > MAX_CAPTURA_BYTES * 4 // 3 + 8:
        raise EntradaInvalida('La captura de pantalla es demasiado grande.')
    try:
        crudo = base64.b64decode(datos, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EntradaInvalida('La captura de pantalla está dañada.') from exc
    if len(crudo) > MAX_CAPTURA_BYTES or not crudo.startswith(_FIRMAS[media_type]):
        raise EntradaInvalida('La captura de pantalla no es válida.')
    return media_type, datos


def _secciones(valor):
    """Partes de la pantalla a las que Gotita puede mover la vista: [(id, título)], sin repetir."""
    if not isinstance(valor, list):
        return []
    vistas, salida = set(), []
    for item in valor[:MAX_SECCIONES]:
        if not isinstance(item, dict):
            continue
        seccion_id = str(item.get('id') or '').strip().lower()
        titulo = _texto(item.get('titulo'), MAX_TITULO)
        if _ID.match(seccion_id) and titulo and seccion_id not in vistas:
            vistas.add(seccion_id)
            salida.append((seccion_id, titulo))
    return salida


def limpiar_turno(data):
    """Revisa y recorta la pregunta que llega. Si algo está mal, avisa con un mensaje para la persona."""
    if not isinstance(data, dict):
        raise EntradaInvalida('Formato no válido.')
    texto = _texto(data.get('texto'), MAX_TEXTO)
    if not texto:
        raise EntradaInvalida('Escribe o di tu pregunta.')
    pantalla = data.get('pantalla') if isinstance(data.get('pantalla'), dict) else {}
    pantalla_id = str(pantalla.get('id') or '').strip().lower()
    historial = []
    for item in (data.get('historial') or [])[-MAX_HISTORIAL:] if isinstance(data.get('historial'), list) else []:
        if not isinstance(item, dict):
            continue
        contenido = _texto(item.get('texto'), MAX_HISTORIAL_TEXTO)
        if contenido:
            historial.append(('assistant' if item.get('rol') == 'gotita' else 'user', contenido))
    idioma = str(data.get('idioma') or '').strip().lower()[:2]
    conversacion = data.get('conversacion')
    if isinstance(conversacion, bool) or not isinstance(conversacion, (int, str)) \
            or not str(conversacion).isdigit() or len(str(conversacion)) > 12:
        conversacion = None
    return {
        'texto': texto,
        'modo': data.get('modo') if data.get('modo') in MODOS else 'sencillo',
        'idioma': idioma if idioma in IDIOMAS else None,
        'pantalla': {
            'id': pantalla_id if _ID.match(pantalla_id) else '',
            'titulo': _texto(pantalla.get('titulo'), MAX_TITULO),
            'texto': _texto(pantalla.get('texto'), MAX_PANTALLA),
            'secciones': _secciones(pantalla.get('secciones')),
        },
        'captura': _captura(data.get('captura')),
        'historial': historial,
        # Las versiones viejas de la app no mandan el número de conversación: siguen la última de hace poco.
        'sigue': bool(historial) and 'conversacion' not in data,
        'conversacion': int(conversacion) if conversacion is not None else None,
        # Idioma en que habló la persona, si lo detectó el dictado de ElevenLabs.
        'idioma_hablado': data.get('idioma_hablado') if data.get('idioma_hablado') in IDIOMAS else None,
        # La llamaron con "Oye Gotita" y el chat está cerrado: la persona está mirando la pantalla.
        'por_voz': data.get('por_voz') is True,
    }


# ── Lo que sale de Gotita ────────────────────────────────────────────────────────────────────

_SECRETOS = (
    re.compile(r'sk-ant-[A-Za-z0-9_\-]{6,}'),
    re.compile(r'eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}'),
    re.compile(r'\b(?:sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}'),
    re.compile(r'(?i)\b(?:contraseña|contrasena|password|passwd|api[ _-]?key|token|secret|secreto)\s*[:=]\s*\S+'),
    re.compile(r'\b[A-Fa-f0-9]{32,}\b'),
    re.compile(r'[A-Za-z0-9+/_\-]{40,}={0,2}'),
)

_CODIGO = re.compile(
    r'^\s*(?:import\s+\w|from\s+\S+\s+import\s|def\s+\w+\s*\(|class\s+\w+\s*[:(]|function\s*\w*\s*\(|'
    r'(?:const|let|var)\s+\w+\s*=|public\s+\w|private\s+\w|#include|<\?php|#!/|\$\s|>>>\s|PS\s*>|C:\\>)'
    r'|<\s*/?\s*(?:script|html|div|body|style|iframe)\b'
    r'|\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\s+(?:\*|(?:INTO|FROM|TABLE|DISTINCT)\b)'
    r'|\b(?:sudo|curl|wget|chmod|rm\s+-rf|pip\s+install|npm\s+install|git\s+clone|python\s+-c|'
    r'powershell|mosquitto_pub)\b'
    r'|=>|\)\s*;|\)\s*\{|\}\s*;|\w\(\)\s*;|\w+\.\w+\([^)]*\)\s*;',
    re.MULTILINE,
)
# Segundo candado: aunque Gotita entiende las groserías, si alguna se le sale, no se muestra ni se lee.
_GROSERIAS = re.compile(
    r'(?i)\b(?:mierdas?|verga|vergas|verg[oó]n\w*|vergazo\w*|vergue\w*|vergo|put[ao]s?|hij[oe]\s*(?:de\s*)?puta|'
    r'hijueput\w*|cerotes?|pendej\w*|culer[oa]s?|cabr[oó]n\w*|chingad\w*|pija|pijud\w*|pijazo\w*|culo|'
    r'fuck\w*|shit\w*|bitch\w*|asshole)\b')
_MARKDOWN_LINEA = re.compile(r'^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+|>\s+)', re.MULTILINE)
_FIN_DE_FRASE = re.compile(r'[.!?…](?=\s)|\n')
MAX_FRASE = 240


def ocultar_secretos(texto):
    for patron in _SECRETOS:
        texto = patron.sub(OCULTO, texto)
    return texto


def parece_codigo(texto):
    return bool(_CODIGO.search(texto))


class FiltroSalida:
    """Va recibiendo lo que escribe Claude y entrega frases limpias, listas para mostrar y leer en voz alta."""

    def __init__(self, idioma='es'):
        self.idioma = idioma if idioma in IDIOMAS else 'es'
        self.buffer = ''
        self.en_codigo = False
        self.aviso_dado = False

    def agregar(self, pedazo):
        self.buffer += pedazo or ''
        return self._extraer(final=False)

    def cerrar(self):
        salida = self._extraer(final=True)
        self.en_codigo = False
        return salida

    def _aviso(self, salida):
        if not self.aviso_dado:
            self.aviso_dado = True
            salida.append(AVISO_CODIGO[self.idioma])

    def _emitir(self, frase, salida):
        frase = frase.replace('**', '').replace('__', '').replace('`', '')
        frase = _MARKDOWN_LINEA.sub('', frase)
        frase = _GROSERIAS.sub('…', frase)
        frase = re.sub(r'\s+', ' ', ocultar_secretos(frase)).strip()
        if not frase:
            return
        if parece_codigo(frase):
            self._aviso(salida)
            return
        salida.append(frase)

    def _extraer(self, final):
        salida = []
        while True:
            if self.en_codigo:
                fin = self.buffer.find('```')
                if fin < 0:
                    self.buffer = '' if final else self.buffer[-2:]
                    break
                self.buffer = self.buffer[fin + 3:]
                self.en_codigo = False
                continue
            inicio = self.buffer.find('```')
            fin_frase = _FIN_DE_FRASE.search(self.buffer)
            corte = fin_frase.end() if fin_frase else -1
            if inicio >= 0 and (corte < 0 or inicio < corte):
                self._emitir(self.buffer[:inicio], salida)
                self.buffer = self.buffer[inicio + 3:]
                self.en_codigo = True
                self._aviso(salida)
                continue
            if corte >= 0:
                self._emitir(self.buffer[:corte], salida)
                self.buffer = self.buffer[corte:]
                continue
            if final:
                self._emitir(self.buffer, salida)
                self.buffer = ''
            elif len(self.buffer) > MAX_FRASE:
                espacio = self.buffer.rfind(' ', 0, MAX_FRASE)
                corte = espacio if espacio > 40 else MAX_FRASE
                self._emitir(self.buffer[:corte], salida)
                self.buffer = self.buffer[corte:]
                continue
            break
        return salida


def filtrar_texto(texto, idioma='es'):
    """Limpia un texto ya completo (los avisos que escribe Gotita)."""
    filtro = FiltroSalida(idioma)
    return ' '.join(filtro.agregar(texto) + filtro.cerrar()).strip()
