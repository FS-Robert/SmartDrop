"""Secciones navegables del buscador global, compartidas por la web y la API móvil."""
from django.urls import reverse

SEARCH_SECTIONS = (
    {
        'titulo': 'Inicio / Estado de Agua',
        'url': 'dashboard',
        'contenido': 'estado de agua, presión actual, calidad del agua, TDS, nivel de tanque, litros disponibles, consumo de agua, uptime del sistema, temperatura, pH, vivienda, lecturas y sensores',
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
        'contenido': 'calidad del agua, TDS, ppm, pH, cloro, turbidez, conductividad, temperatura, buena, media, mala, óptima, aceptable y anomalías',
    },
    {
        'titulo': 'Nivel de Tanque',
        'url': 'tanque',
        'contenido': 'nivel de tanque, tanque, porcentaje, litros, capacidad, autonomía, horas restantes, bomba, suministro y última lectura',
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
)


def _en(texto):
    from . import i18n
    return ((i18n.catalogo('en') or {}).get('frases') or {}).get(texto, texto)


def matching_sections(search_term, is_admin, idioma='es'):
    """Secciones cuyo título o contenido incluye `search_term` (ya en casefold), en español o en inglés.

    El resultado se devuelve en el idioma del usuario para que el resaltado de la búsqueda coincida.
    """
    if not search_term:
        return []
    sections = SEARCH_SECTIONS + ADMIN_SEARCH_SECTIONS if is_admin else SEARCH_SECTIONS
    results = []
    for section in sections:
        textos = (section['titulo'], section['contenido'], _en(section['titulo']), _en(section['contenido']))
        if any(search_term in texto.casefold() for texto in textos):
            titulo, contenido = (textos[2], textos[3]) if idioma == 'en' else (textos[0], textos[1])
            results.append({'titulo': titulo, 'url': reverse(section['url']), 'contenido': contenido})
    return results
