"""Registro manual de consumo y nivel (PB054).

/registro-manual/   el operador (administrador) escribe a mano el consumo en litros y el nivel del tanque en %
                    cuando un sensor no está disponible. Se guarda en la tabla registro_manual, aparte de las
                    lecturas de los sensores, así no cambia gráficas, alertas ni la IA.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import RegistroManual
from .queries import is_admin, owner_id

CONSUMO_MAXIMO_L = Decimal('100000')


def _numero(texto):
    try:
        return Decimal(str(texto).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None


def validar(datos, ahora=None):
    """Revisa lo que escribió el operador. Devuelve (limpios, errores)."""
    ahora = ahora or timezone.now()
    errores = {}
    consumo_txt = (datos.get('consumo_litros') or '').strip()
    nivel_txt = (datos.get('nivel_pct') or '').strip()

    if not consumo_txt:
        errores['consumo_litros'] = 'El consumo es obligatorio.'
    if not nivel_txt:
        errores['nivel_pct'] = 'El nivel es obligatorio.'

    consumo = _numero(consumo_txt) if consumo_txt else None
    nivel = _numero(nivel_txt) if nivel_txt else None
    if consumo_txt and (consumo is None or not consumo.is_finite() or not 0 <= consumo <= CONSUMO_MAXIMO_L):
        errores['consumo_litros'] = f'Escribe un consumo entre 0 y {CONSUMO_MAXIMO_L:,.0f} litros.'
    if nivel_txt and (nivel is None or not nivel.is_finite() or not 0 <= nivel <= 100):
        errores['nivel_pct'] = 'Escribe un nivel entre 0 y 100 %.'

    fecha = ahora
    fecha_txt = (datos.get('fecha_medicion') or '').strip()
    if fecha_txt:
        try:
            fecha = timezone.make_aware(datetime.strptime(fecha_txt, '%Y-%m-%dT%H:%M'))
        except ValueError:
            errores['fecha_medicion'] = 'La fecha no es válida.'
        else:
            if fecha > ahora + timedelta(minutes=5):
                errores['fecha_medicion'] = 'La fecha no puede ser en el futuro.'

    limpios = {
        'ubicacion': (datos.get('ubicacion') or '').strip()[:120],
        'observacion': (datos.get('observacion') or '').strip()[:300],
        'consumo_litros': consumo.quantize(Decimal('0.01')) if consumo is not None and 'consumo_litros' not in errores else None,
        'nivel_pct': nivel.quantize(Decimal('0.01')) if nivel is not None and 'nivel_pct' not in errores else None,
        'fecha_medicion': fecha,
    }
    return limpios, errores


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def registro_manual(request):
    if not is_admin(request.user):
        return redirect('dashboard')

    valores, errores = {}, {}
    if request.method == 'POST':
        valores = request.POST
        limpios, errores = validar(request.POST)
        if errores:
            messages.error(request, 'Revisa los campos marcados: ' + ' '.join(errores.values()))
        else:
            nombre = f'{getattr(request.user, "nombre", "")} {getattr(request.user, "apellido", "")}'.strip()
            registro = RegistroManual.objects.create(
                **limpios, id_usuario=owner_id(request.user), operador=nombre or request.user.get_username(),
            )
            messages.success(
                request,
                f'Registro guardado: {registro.consumo_litros} L y nivel {registro.nivel_pct} % '
                f'({timezone.localtime(registro.fecha_medicion):%d/%m/%Y %H:%M}).',
            )
            return redirect('registro_manual')

    return render(request, 'App/registro_manual.html', {
        'valores': valores,
        'errores': errores,
        'registros': RegistroManual.objects.all()[:20],
        'ahora': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
    })
