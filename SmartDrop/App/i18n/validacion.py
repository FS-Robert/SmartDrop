"""Reglas que debe cumplir el catálogo de traducción (las usan el comando i18n_catalogo y los tests)."""
import re

HUECO = re.compile(r'\{\d+\}')


def errores_catalogo(catalogo):
    """Lista de problemas del catálogo (vacía si está bien)."""
    errores = []
    frases = catalogo.get('frases') or {}
    plantillas = catalogo.get('plantillas') or {}
    mayusculas = {es.upper(): en.upper() for es, en in frases.items()}
    for es, en in frases.items():
        if not str(en).strip():
            errores.append(f'Frase sin traducción: {es!r}')
        if HUECO.search(es):
            errores.append(f'La frase tiene huecos {{n}}; debe ir en "plantillas": {es!r}')
        # Sin cadenas: una traducción no puede ser otra clave con distinto valor (la traducción automática alternaría).
        if (en in frases and frases[en] != en) or (en.isupper() and mayusculas.get(en, en) != en):
            errores.append(f'Traducción encadenada: {es!r} -> {en!r}')
    for es, en in plantillas.items():
        if not HUECO.search(es):
            errores.append(f'Plantilla sin huecos (debe ir en "frases"): {es!r}')
        if sorted(HUECO.findall(es)) != sorted(HUECO.findall(en)):
            errores.append(f'Los huecos no coinciden: {es!r} -> {en!r}')
    return errores
