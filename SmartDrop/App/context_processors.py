from . import i18n, preferencias

IDIOMA_COOKIE = 'sd_idioma'


def preferencias_ui(request):
    """Tema e idioma del usuario para todas las plantillas (base.html los aplica antes de pintar).

    Sin sesión (login, registro) el idioma sale de la cookie que se guarda al cambiarlo en el perfil.
    """
    user = getattr(request, 'user', None)
    contexto = {'ui_idioma': request.COOKIES.get(IDIOMA_COOKIE, 'es')}
    if user and user.is_authenticated:
        try:
            prefs = preferencias.preferencias_de(user)
        except Exception:
            prefs = None
        if prefs is not None:
            contexto['ui_prefs'] = {'tema': 'dark' if prefs.modo_oscuro else 'light', 'idioma': prefs.idioma}
            contexto['ui_idioma'] = prefs.idioma
    if contexto['ui_idioma'] not in ('es', 'en'):
        contexto['ui_idioma'] = 'es'
    if contexto['ui_idioma'] == 'en':
        contexto['ui_i18n_version'] = (i18n.catalogo('en') or {}).get('version', 0)
    return contexto
