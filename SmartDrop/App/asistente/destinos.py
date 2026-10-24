"""Pantallas a las que Gotita puede llevar a cada persona, según sea admin o usuario, en la web o la app.

Login, registro y cerrar sesión no están aquí a propósito: Gotita nunca abre ni cierra sesiones.
La pantalla de válvulas sí está para el admin, pero solo para verla: Gotita no tiene ninguna herramienta
que mande comandos (el botón y su confirmación siguen siendo del admin).
"""
from django.urls import NoReverseMatch, reverse

USUARIO = 'user'
ADMIN = 'admin'
WEB = 'web'
APP = 'app'
PLATAFORMAS = (WEB, APP)

# pantalla: (nombre, qué muestra, quién puede verla, dirección en la web, si existe en la app)
DESTINOS = {
    'inicio': ('Inicio', 'resumen del agua de la vivienda', {USUARIO}, 'dashboard', True),
    'estado_sistema': ('Estado del sistema', 'sensores y su conexión', {USUARIO}, 'estado_sistema', False),
    'tanque': ('Tanque', 'nivel del tanque en porcentaje y litros', {USUARIO}, 'tanque', True),
    'presion': ('Presión', 'presión del agua en la tubería', {USUARIO}, 'presion', True),
    'calidad': ('Calidad', 'calidad del agua (TDS)', {USUARIO}, 'calidad', True),
    'consumo': ('Consumo', 'litros gastados por día y mes', {USUARIO}, 'consumo', True),
    'retroalimentacion': ('Retroalimentación', 'comparación del consumo con otros hogares', {USUARIO},
                          'retroalimentacion', True),
    'recomendaciones': ('Consejos de ahorro', 'consejos para gastar menos agua', {USUARIO}, 'recomendaciones', True),
    'reportar': ('Reportar un problema', 'formulario y lista de reportes propios', {USUARIO}, 'reportes_panel', True),
    'comunidad': ('Reportes de la comunidad', 'problemas reportados por vecinos', {USUARIO},
                  'reportes_comunidad', True),
    'alertas': ('Alertas', 'historial de alertas', {USUARIO, ADMIN}, 'alertas_historial', True),
    'perfil': ('Mi perfil', 'datos personales y preferencias', {USUARIO}, 'usuario', True),
    'configuracion': ('Configuración', 'tema, idioma y notificaciones', {USUARIO, ADMIN}, None, True),
    'buscar': ('Buscar', 'buscador de pantallas, sensores, reportes y alertas', {USUARIO, ADMIN}, 'busqueda_global',
               True),
    'vincular_vivienda': ('Vincular vivienda', 'agregar una vivienda con su NIC', {USUARIO}, 'vincular_vivienda', True),
    'panel': ('Panel general', 'estado de todo el sistema', {ADMIN}, 'admin_panel', True),
    'graficas': ('Gráficas y monitoreo', 'gráficas de flujo, presión y nivel', {ADMIN}, None, True),
    'valvulas': ('Electroválvulas', 'estado e historial de las válvulas (solo para ver)', {ADMIN}, 'valvulas', True),
    'prediccion_suministro': ('Predicción de suministro', 'pronóstico del tanque y del consumo por zona', {ADMIN},
                              'ml_engine:dashboard', True),
    'prediccion_fugas': ('Predicción de fugas', 'viviendas con posible fuga', {ADMIN}, 'ml_engine:leaks', True),
    'reportes_admin': ('Problemas reportados', 'reportes de los usuarios para atender', {ADMIN}, 'admin_reportes', True),
}


def rol_de(user):
    from ..queries import is_admin
    return ADMIN if is_admin(user) else USUARIO


def disponibles(rol, plataforma):
    """Pantallas permitidas para esta persona, siempre en el mismo orden (así Claude cobra menos)."""
    return [
        clave for clave, (_, _, roles, ruta_web, en_app) in DESTINOS.items()
        if rol in roles and (ruta_web if plataforma == WEB else en_app)
    ]


def permitido(destino, rol, plataforma):
    return destino in disponibles(rol, plataforma)


def nombre(destino):
    return DESTINOS[destino][0] if destino in DESTINOS else destino


def url_web(destino):
    ruta = DESTINOS.get(destino, (None, None, None, None, None))[3]
    if not ruta:
        return None
    try:
        return reverse(ruta)
    except NoReverseMatch:
        return None


def descripcion_para_claude(rol, plataforma):
    return '\n'.join(f'- {clave}: {DESTINOS[clave][0]} ({DESTINOS[clave][1]})' for clave in disponibles(rol, plataforma))
