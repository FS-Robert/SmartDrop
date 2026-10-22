"""Revisiones de configuración que aparecen en `manage.py check`, migrate y collectstatic (los corre el despliegue)."""
import base64
import json

from django.conf import settings
from django.core.checks import Warning, register


def _rol_de_la_clave(clave):
    """'anon', 'service_role'… según la clave de Supabase (JWT antiguo o clave sb_* nueva); None si no se sabe."""
    if clave.startswith('sb_publishable_'):
        return 'anon'
    if clave.startswith('sb_secret_'):
        return 'service_role'
    partes = clave.split('.')
    if len(partes) != 3:
        return None
    try:
        contenido = partes[1] + '=' * (-len(partes[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(contenido)).get('role')
    except (ValueError, UnicodeDecodeError):
        return None


@register(deploy=False)
def revisar_seguridad(app_configs, **kwargs):
    avisos = []
    clave = (settings.SUPABASE_KEY or '').strip()
    if clave and _rol_de_la_clave(clave) == 'anon':
        avisos.append(Warning(
            'SUPABASE_KEY es la clave pública (anon) de Supabase.',
            hint='El servidor debe usar la clave service_role (secreta). Con RLS activado '
                 '(supabase/migraciones/2026-10-22_seguridad.sql) la clave anon no puede leer las tablas.',
            id='smartdrop.W001',
        ))
    if not settings.DEBUG and not settings.LECTURAS_API_KEY:
        avisos.append(Warning(
            'Falta LECTURAS_API_KEY: /api/lecturas/ rechazará todas las lecturas del ESP32.',
            hint='Agrégala al .env (deploy/actualizar.sh la genera) y configura la misma clave en el ESP32.',
            id='smartdrop.W002',
        ))
    return avisos
