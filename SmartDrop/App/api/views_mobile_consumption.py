import calendar
from collections import defaultdict
from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from ..queries import owned_consumption
from .permissions import IsAuthenticatedUser

_DIAS_ES = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')
_MESES_ES = (
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)


def _fecha_consumo(raw_date):
    """Replica la conversión de la web: fecha ISO -> date local."""
    row_date = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
    if row_date.tzinfo:
        row_date = timezone.localtime(row_date)
    return row_date.date()


class MobileConsumoView(APIView):
    """Replica la agregación de la vista web `consumo` para que la app y la web
    muestren exactamente los mismos datos en día / semana / mes."""

    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        periodo = request.query_params.get('periodo', 'dia')
        if periodo not in ('dia', 'semana', 'mes'):
            return Response({'error': 'periodo inválido, usa dia, semana o mes.'}, status=400)

        try:
            _, rows = owned_consumption(request.user, timezone.localdate().replace(day=1))
        except Exception:
            rows = []

        today = timezone.localdate()
        selected_month = today.replace(day=1)

        parsed_rows = []
        for row in rows:
            raw_date = row.get('fecha')
            raw_value = row.get('consumo_total')
            if not raw_date or raw_value is None:
                continue
            try:
                parsed_rows.append((_fecha_consumo(raw_date), float(raw_value)))
            except (TypeError, ValueError, OverflowError):
                continue

        month_values = defaultdict(float)
        day_values = []
        for row_date, value in parsed_rows:
            if row_date == today:
                day_values.append(value)
            month_values[row_date.replace(day=1)] += value

        days_in_month = calendar.monthrange(selected_month.year, selected_month.month)[1]
        month_days = [selected_month + timedelta(days=offset) for offset in range(days_in_month)]
        daily_values = {day: 0.0 for day in month_days}
        for row_date, value in parsed_rows:
            if row_date in daily_values:
                daily_values[row_date] += value

        month_week_values = [0.0, 0.0, 0.0, 0.0]
        for row_date, value in parsed_rows:
            if row_date.year == selected_month.year and row_date.month == selected_month.month:
                week_index = min((row_date.day - 1) // 7, 3)
                month_week_values[week_index] += value

        if periodo == 'dia':
            serie = [
                {'fecha': f'{_DIAS_ES[day.weekday()]} {day.day:02d}/{day.month:02d}', 'litros': round(daily_values[day], 2)}
                for day in month_days[:7]
            ]
            consumo_total = round(sum(day_values), 2)
        elif periodo == 'semana':
            serie = [
                {'fecha': f'Semana {index + 1}', 'litros': round(value, 2)}
                for index, value in enumerate(month_week_values)
            ]
            consumo_total = round(sum(month_week_values), 2)
        else:  # mes
            serie = [
                {
                    'fecha': _MESES_ES[month - 1].capitalize()[:3],
                    'litros': round(month_values.get(selected_month.replace(month=month), 0), 2),
                }
                for month in range(1, 13)
            ]
            consumo_total = round(month_values.get(selected_month, 0), 2)

        previous_week_start = today - timedelta(days=13)
        previous_week_end = today - timedelta(days=7)
        previous_week_total = round(sum(
            value for row_date, value in parsed_rows
            if previous_week_start <= row_date <= previous_week_end
        ), 2)
        variation = round(((consumo_total - previous_week_total) / previous_week_total) * 100, 2) if previous_week_total else None

        return Response({
            'periodo': periodo,
            'unidad': 'L',
            'consumo_total': consumo_total,
            'estado_texto': 'SIN DATOS DE CONSUMO' if not parsed_rows else 'CONSUMO REGISTRADO',
            'color': 'gris' if not parsed_rows else 'verde',
            'comparacion': {
                'porcentaje': variation,
                'texto': 'Sin datos comparables' if variation is None else f'{variation:+.2f}% vs semana anterior',
            },
            'serie': serie,
            'punto_maximo': max(serie, key=lambda item: item['litros']) if serie else None,
        })


class MobileRetroalimentacionView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response({
            'consumo_hoy_litros': 0,
            'promedio_habitual_litros': None,
            'estado': 'normal',
            'color': 'verde',
            'mensaje': 'Tu consumo está dentro de lo normal.',
            'comparacion_mes': {'porcentaje': None, 'texto': 'Sin datos suficientes todavía.'},
            'racha_dias': 0,
            'racha_texto': 'Empieza tu racha hoy',
        })


class MobileRecomendacionesView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response({
            'saludo': 'Basado en tu consumo reciente, tenemos estas sugerencias para ti.',
            'tips': [
                {'id': 'cerrar_llave', 'titulo': 'Cierra la llave mientras te cepillas', 'impacto': 'Ahorra agua diariamente'},
                {'id': 'reducir_ducha', 'titulo': 'Reduce el tiempo de ducha', 'impacto': 'Disminuye tu consumo'},
            ],
        })
