"""Manual de usuario: /manual/ en la web (con el menú) y /manual/?app=1 dentro de la app (sin menú).

El contenido es el mismo para las dos y está en App/Templates/App/_manual_usuario_contenido.html.
La versión de la app no pide sesión: la app la abre en una pantalla propia, sin las cookies de la web, y el manual
no tiene datos de nadie.
"""
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def manual_usuario(request):
    if request.GET.get('app') == '1':
        return render(request, 'App/manual_usuario_app.html', {'oscuro': request.GET.get('tema') == 'oscuro'})
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render(request, 'App/manual_usuario.html')
