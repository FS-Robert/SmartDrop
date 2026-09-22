"""Vistas web del dashboard de predicción (Sección 9): 100% admin-only."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ml_engine.models import Zone


def _require_admin(request):
    return getattr(request.user, 'rol_id', None) == 2


@login_required(login_url='login')
def ml_dashboard(request):
    if not _require_admin(request):
        return redirect('dashboard')

    zonas = Zone.objects.all().order_by('name')
    context = {'zonas': zonas}
    return render(request, 'ml_engine/dashboard.html', context)


@login_required(login_url='login')
def ml_zone_detail(request, zone_id: int):
    if not _require_admin(request):
        return redirect('dashboard')

    zone = get_object_or_404(Zone, id=zone_id)
    homes = zone.homes.filter(activo=True).order_by('etiqueta')
    context = {'zone': zone, 'homes': homes}
    return render(request, 'ml_engine/zone_detail.html', context)
