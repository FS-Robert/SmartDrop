from collections import defaultdict
from datetime import datetime, timedelta

from rest_framework.response import Response
from rest_framework.views import APIView

from ..views import _owned_consumption
from .permissions import IsAuthenticatedUser


class MobileConsumoView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        periodo = request.query_params.get('periodo', 'dia')
        if periodo not in ('dia', 'semana', 'mes'):
            return Response({'error': 'periodo inválido, usa dia, semana o mes.'}, status=400)

        _, rows = _owned_consumption(request)
        grouped = defaultdict(float)
        for row in rows:
            raw_date = row.get('fecha') or row.get('fecha_registro')
            if not raw_date:
                continue
            label = str(raw_date)[:10]
            grouped[label] += float(row.get('consumo_total') or row.get('consumo_promedio') or 0)

        series = [
            {'fecha': label, 'litros': round(value, 2)}
            for label, value in sorted(grouped.items())
        ]
        total = round(sum(item['litros'] for item in series), 2)
        return Response({
            'periodo': periodo,
            'unidad': 'L',
            'consumo_total': total,
            'estado_texto': 'CONSUMO REGISTRADO' if total else 'SIN ACTIVIDAD REGISTRADA',
            'color': 'gris' if not total else 'verde',
            'comparacion': {'porcentaje': None, 'texto': 'Sin datos suficientes para comparar todavía.'},
            'serie': series,
            'punto_maximo': max(series, key=lambda item: item['litros']) if series else None,
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
