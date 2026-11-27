"""Secciones navegables del buscador global, compartidas por la web y la API móvil."""
from django.urls import reverse

SEARCH_SECTIONS = (
    {
        'titulo': 'Inicio / Estado de Agua',
        'url': 'dashboard',
        'contenido': 'estado de agua, presión actual, calidad del agua, TDS, nivel de tanque, litros disponibles, consumo de agua, uptime del sistema, pH, vivienda, lecturas y sensores',
    },
    {
        'titulo': 'Retroalimentación e Indicadores de Consumo',
        'url': 'retroalimentacion',
        'contenido': 'retroalimentación, indicadores de consumo, consumo total, consumo promedio, diferencia de consumo, fecha de registro, análisis y comparación',
    },
    {
        'titulo': 'Presión del Agua',
        'url': 'presion',
        'contenido': 'presión del agua, presión, bar, psi, valor, mínimo del día, máximo del día, promedio del día, válvula, lectura y actualizado ahora',
    },
    {
        'titulo': 'Calidad del Agua - TDS',
        'url': 'calidad',
        'contenido': 'calidad del agua, TDS, ppm, pH, cloro, turbidez, conductividad, buena, media, mala, óptima, aceptable y anomalías',
    },
    {
        'titulo': 'Nivel de Tanque',
        'url': 'tanque',
        'contenido': 'nivel de tanque, tanque, porcentaje, litros, capacidad, autonomía, horas restantes, suministro y última lectura',
    },
    {
        'titulo': 'Consumo Diario',
        'url': 'consumo',
        'contenido': 'consumo diario, consumo de agua, litros, L, fecha, mes, consumo total, consumo promedio, consumo máximo, consumo mínimo, período y estado de pago',
    },
    {
        'titulo': 'Recomendaciones de Ahorro',
        'url': 'recomendaciones',
        'contenido': 'recomendaciones de ahorro, ahorrar agua, impacto, reutilizar agua, reparar fugas, suministro y consumo elevado',
    },
    {
        'titulo': 'Mi Perfil',
        'url': 'usuario',
        'contenido': 'mi perfil, usuario, nombre, correo, dirección, ciudad, teléfono, miembro desde, notificaciones y preferencias',
    },
    # PB045: la ayuda también se encuentra desde el buscador.
    {
        'titulo': 'Manual de usuario',
        'url': 'manual_usuario',
        'contenido': 'manual, ayuda, guía, tutorial, cómo usar SmartDrop, crear cuenta, iniciar sesión, vincular vivienda, '
                     'pantalla de inicio, tanque, presión, calidad, consumo, cómo ahorrar, alertas, notificaciones, '
                     'reportar un problema, Gotita, perfil, preferencias, cuidar tu cuenta y preguntas frecuentes',
    },
    {
        'titulo': 'Reportar un problema',
        'url': 'reportes_panel',
        'solo_web': True,
        'contenido': 'reportar un problema, no llega agua, fuga, baja presión, mala calidad, tanque, facturación, evidencia, fotos y chat con el equipo',
    },
)

ADMIN_SEARCH_SECTIONS = (
    {
        'titulo': 'Panel Administrativo',
        'url': 'admin_panel',
        'contenido': 'panel administrativo, usuarios, viviendas, sensores, lecturas, monitoreo global, datos recientes y supervisión',
    },
    {
        'titulo': 'Electroválvulas',
        'url': 'valvulas',
        'contenido': 'electroválvulas, abrir, cerrar, estado actual, estado operativo, MQTT, historial, logs, acciones manuales y automáticas',
    },
    {
        'titulo': 'Problemas reportados',
        'url': 'admin_reportes',
        'solo_web': True,
        'contenido': 'problemas reportados, reportes de usuarios, cortes de agua, duración del corte, zona, prioridad, estado, respuesta y CSV',
    },
    {
        'titulo': 'Historial de alertas',
        'url': 'alertas_historial',
        'solo_web': True,
        'contenido': 'historial de alertas, alertas, fugas, calidad, estado de seguimiento, pendiente, confirmada, descartada, resuelta y CSV',
    },
    {
        'titulo': 'Consumo por usuario',
        'url': 'admin_consumo',
        'solo_web': True,
        'contenido': 'consumo por usuario, consumo por zona, metros cúbicos, rango de fechas, comparación y CSV',
    },
    {
        'titulo': 'Registro manual',
        'url': 'registro_manual',
        'solo_web': True,
        'contenido': 'registro manual, anotar consumo a mano, nivel del tanque, sensor no disponible y últimos registros',
    },
    {
        'titulo': 'Manual del administrador',
        'url': 'manual_admin',
        'contenido': 'manual del administrador, manual, ayuda, guía, panel general, electroválvulas, consumo por usuario, '
                     'predicción de suministro, predicción de fugas, problemas reportados, historial de alertas, '
                     'registro manual, app del administrador, seguridad y si algo no funciona',
    },
    {
        'titulo': 'Manual técnico',
        'url': 'manual_tecnico',
        'solo_web': True,
        'contenido': 'manual técnico, manual, arquitectura, instalación, base de datos, API, endpoints, Postman, MQTT y despliegue',
    },
)


def _en(texto):
    from . import i18n
    return ((i18n.catalogo('en') or {}).get('frases') or {}).get(texto, texto)


def matching_sections(search_term, is_admin, idioma='es', incluir_solo_web=True):
    """Secciones cuyo título o contenido incluye `search_term` (ya en casefold), en español o en inglés.

    El resultado se devuelve en el idioma del usuario para que el resaltado de la búsqueda coincida.
    La app pide `incluir_solo_web=False`: las pantallas que solo existen en la web no se le mandan
    (los manuales sí, porque la app los abre en su pantalla de manual).
    """
    if not search_term:
        return []
    sections = SEARCH_SECTIONS + ADMIN_SEARCH_SECTIONS if is_admin else SEARCH_SECTIONS
    results = []
    for section in sections:
        if section.get('solo_web') and not incluir_solo_web:
            continue
        textos = (section['titulo'], section['contenido'], _en(section['titulo']), _en(section['contenido']))
        if any(search_term in texto.casefold() for texto in textos):
            titulo, contenido = (textos[2], textos[3]) if idioma == 'en' else (textos[0], textos[1])
            results.append({'titulo': titulo, 'url': reverse(section['url']), 'contenido': contenido})
    return results
