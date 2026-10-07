"""Catálogo de traducción español→inglés compartido por la web (static/App/js/i18n.js) y la app Android.

Formato: {"version": n, "frases": {es: en}, "plantillas": {"Hay {0} alertas": "There are {0} alerts"}}.
La app trae una copia en sus assets y descarga esta versión al cambiar de idioma.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

IDIOMAS = ('en',)
CACHE_SECONDS = 3600


@lru_cache(maxsize=4)
def _catalogo(idioma: str, mtime: float) -> dict:
    return json.loads((Path(__file__).parent / f'{idioma}.json').read_text(encoding='utf-8'))


def catalogo(idioma: str) -> dict | None:
    if idioma not in IDIOMAS:
        return None
    path = Path(__file__).parent / f'{idioma}.json'
    return _catalogo(idioma, path.stat().st_mtime)


def _cacheable(response, data):
    response['Cache-Control'] = f'public, max-age={CACHE_SECONDS}'
    response['ETag'] = f'"i18n-{data.get("version", 0)}"'
    return response


@require_GET
def catalogo_json(request, idioma):
    """GET /api/i18n/<idioma>/ — catálogo para la app móvil (público: no contiene datos de usuarios)."""
    data = catalogo(idioma)
    if data is None:
        return JsonResponse({'error': 'Idioma no disponible.'}, status=404)
    return _cacheable(JsonResponse(data, json_dumps_params={'ensure_ascii': False}), data)


@require_GET
def catalogo_js(request, idioma):
    """GET /i18n/<idioma>.js — el mismo catálogo como script para la web (se carga antes de pintar la página)."""
    data = catalogo(idioma)
    if data is None:
        return HttpResponse('window.SD_I18N = null;', content_type='application/javascript', status=404)
    body = 'window.SD_I18N = ' + json.dumps(data, ensure_ascii=False) + ';'
    return _cacheable(HttpResponse(body, content_type='application/javascript; charset=utf-8'), data)
