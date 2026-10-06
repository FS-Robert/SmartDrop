"""Vistas web del panel de predicción (admin): suministro (tanques) y fugas."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from ml_engine.models import Zone


def _require_admin(request):
    return getattr(request.user, 'rol_id', None) == 2


@login_required(login_url='login')
@ensure_csrf_cookie
def ml_dashboard(request):
    """Predicción de suministro: lista de tanques/viviendas con su riesgo y el botón 'Realizar predicciones'."""
    if not _require_admin(request):
        return redirect('dashboard')
    return render(request, 'ml_engine/dashboard.html')


@login_required(login_url='login')
def ml_zone_detail(request, zone_id: int):
    if not _require_admin(request):
        return redirect('dashboard')

    zone = get_object_or_404(Zone, id=zone_id)
    homes = zone.homes.filter(activo=True).order_by('etiqueta')
    return render(request, 'ml_engine/zone_detail.html', {'zone': zone, 'homes': homes})


@login_required(login_url='login')
@ensure_csrf_cookie
def ml_leaks(request):
    """Predicción de fugas por vivienda, con el estado del monitor automático."""
    if not _require_admin(request):
        return redirect('dashboard')
    return render(request, 'ml_engine/leaks.html')