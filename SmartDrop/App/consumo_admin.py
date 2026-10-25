"""Reporte de consumo por usuario y por zona para el administrador (PB032).

De dónde sale cada número (la misma regla que ve cada usuario en su pantalla de Consumo):
* Si un día tiene consumo registrado en la tabla `consumo` de Supabase, se usa ese.
* Si no, se usa lo que midió el sensor de flujo ese día. Para no pedirle a Supabase miles de lecturas, se toma
  de la copia local del motor de predicción (ml_engine), que la sincroniza cada 10 minutos por hora y vivienda.

Todo se muestra en metros cúbicos (1 m³ = 1000 litros). El límite de la comunidad está en m³ por mes y se
ajusta a la cantidad de días que se están viendo (por ejemplo, la mitad si son 15 días).
"""
import logging
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import supabase_client
from .models import AjusteComunidad
from .queries import consumption_date, users_by_id

logger = logging.getLogger(__name__)

LIMITE_CLAVE = 'limite_consumo_m3_mes'
DIAS_DEL_MES = 30
# Un consumo es "anormal" si el promedio diario sube esto o más frente al periodo anterior del mismo largo.
AUMENTO_ANORMAL = 0.4
MINIMO_PARA_COMPARAR_M3 = 0.05   # con menos de 50 litros en el periodo anterior no se compara (no dice nada)
CACHE_SEGUNDOS = 120
MAX_DIAS = 366


def limite_mensual():
    """Límite de consumo de la comunidad, en m³ por mes por vivienda."""
    guardado = AjusteComunidad.objects.filter(clave=LIMITE_CLAVE).values_list('valor', flat=True).first()
    if guardado is not None:
        return guardado
    return float(getattr(settings, 'CONSUMO_LIMITE_M3_MES', 20))


def guardar_limite(valor, id_usuario=None):
    AjusteComunidad.objects.update_or_create(
        clave=LIMITE_CLAVE, defaults={'valor': float(valor), 'id_usuario': id_usuario})


def periodo_desde_parametros(params, hoy=None):
    """(desde, hasta, etiqueta) a partir de ?mes=AAAA-MM o ?desde=AAAA-MM-DD&hasta=AAAA-MM-DD.

    Sin nada, el mes en curso hasta hoy. Fechas al revés se acomodan; el futuro se recorta a hoy.
    """
    hoy = hoy or timezone.localdate()
    desde_txt, hasta_txt, mes_txt = params.get('desde'), params.get('hasta'), params.get('mes')
    try:
        if desde_txt and hasta_txt:
            desde = datetime.strptime(desde_txt, '%Y-%m-%d').date()
            hasta = datetime.strptime(hasta_txt, '%Y-%m-%d').date()
            if hasta < desde:
                desde, hasta = hasta, desde
            hasta = min(hasta, hoy)
            desde = max(min(desde, hasta), hasta - timedelta(days=MAX_DIAS - 1))
            return desde, hasta, f'{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}'
        if mes_txt:
            inicio = datetime.strptime(mes_txt, '%Y-%m').date().replace(day=1)
            fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            fin = min(fin, hoy)
            if fin >= inicio:
                return inicio, fin, _nombre_mes(inicio)
    except ValueError:
        pass
    inicio = hoy.replace(day=1)
    return inicio, hoy, _nombre_mes(inicio)


_MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
          'noviembre', 'diciembre')


_DIAS_SEMANA = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')


def _nombre_mes(dia):
    return f'{_MESES[dia.month - 1].capitalize()} {dia.year}'


def _dias(desde, hasta):
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]


def _medianoche(dia):
    return timezone.make_aware(datetime.combine(dia, dt_time.min))


# ── Lecturas ────────────────────────────────────────────────────────────────

def _viviendas():
    return supabase_client.select(
        'vivienda', 'id_vivienda,nic,direccion,id_usuario_propietario,nombre_completo_titular',
        {'order': 'id_vivienda.asc', 'limit': '2000'},
    )


def _registrado(ids_vivienda, desde, hasta):
    """{(id_vivienda, día): litros} de la tabla `consumo` en el rango."""
    if not ids_vivienda:
        return {}
    filas = supabase_client.select('consumo', 'id_vivienda,fecha,consumo_total', {
        'id_vivienda': supabase_client.ids_in([str(i) for i in ids_vivienda]),
        'and': f'(fecha.gte.{_medianoche(desde).isoformat()},fecha.lt.{_medianoche(hasta + timedelta(days=1)).isoformat()})',
        'limit': '20000',
    })
    total = defaultdict(float)
    for fila in filas:
        try:
            total[(int(fila['id_vivienda']), consumption_date(fila['fecha']))] += float(fila.get('consumo_total') or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return total


def _medido(desde, hasta):
    """({(id_vivienda, día): litros}, {id_vivienda: zona}, última hora sincronizada) desde la copia local."""
    from ml_engine.models import ConsumptionAggregate, Home

    hogares = {h.pk: h for h in Home.objects.filter(source='real', source_ref_vivienda_id__isnull=False)}
    zonas = {h.source_ref_vivienda_id: (h.meta or {}).get('zona') or h.zone.name for h in hogares.values()}
    litros = defaultdict(float)
    ultima = None
    filas = ConsumptionAggregate.objects.filter(
        home_id__in=hogares.keys(), period='hourly',
        period_start__gte=_medianoche(desde), period_start__lt=_medianoche(hasta + timedelta(days=1)),
    ).values_list('home_id', 'period_start', 'litros')
    for home_id, inicio, valor in filas.iterator():
        dia = timezone.localtime(inicio).date()
        litros[(hogares[home_id].source_ref_vivienda_id, dia)] += max(0.0, valor or 0.0)
        ultima = inicio if ultima is None or inicio > ultima else ultima
    return litros, zonas, ultima


def _consumo_por_dia(ids_vivienda, desde, hasta):
    """{id_vivienda: {día: litros}} con la regla: registrado si hay, si no lo medido por el flujo."""
    registrado = _registrado(ids_vivienda, desde, hasta)
    medido, zonas, ultima = _medido(desde, hasta)
    por_dia = {}
    for id_vivienda in ids_vivienda:
        dias = {}
        for dia in _dias(desde, hasta):
            valor = registrado.get((id_vivienda, dia), 0.0)
            dias[dia] = valor if valor > 0 else medido.get((id_vivienda, dia), 0.0)
        por_dia[id_vivienda] = dias
    return por_dia, zonas, ultima


# ── Reporte ─────────────────────────────────────────────────────────────────

def _m3(litros):
    return round(litros / 1000.0, 3)


def reporte(desde, hasta, limite_mes=None):
    """Todo lo que muestra la pantalla y lo que va en el Excel."""
    limite_mes = limite_mensual() if limite_mes is None else limite_mes
    clave = f'consumo_admin:{desde}:{hasta}:{limite_mes}'
    guardado = cache.get(clave)
    if guardado is not None:
        return guardado

    viviendas = _viviendas()
    ids = [int(v['id_vivienda']) for v in viviendas if str(v.get('id_vivienda') or '').isdigit()]
    dias = len(_dias(desde, hasta))
    anterior_hasta = desde - timedelta(days=1)
    anterior_desde = anterior_hasta - timedelta(days=dias - 1)

    actual, zonas, ultima = _consumo_por_dia(ids, desde, hasta)
    anterior, _, _ = _consumo_por_dia(ids, anterior_desde, anterior_hasta)
    duenos = users_by_id((v.get('id_usuario_propietario') for v in viviendas), 'id_usuario,nombre,apellido,correo')

    limite_periodo = round(limite_mes * dias / DIAS_DEL_MES, 3)
    usuarios = []
    for vivienda in viviendas:
        id_vivienda = int(vivienda['id_vivienda'])
        dueno = duenos.get(vivienda.get('id_usuario_propietario')) or {}
        nombre = f"{dueno.get('nombre', '')} {dueno.get('apellido', '')}".strip()
        m3 = _m3(sum(actual.get(id_vivienda, {}).values()))
        m3_anterior = _m3(sum(anterior.get(id_vivienda, {}).values()))
        cambio = round((m3 - m3_anterior) / m3_anterior, 3) if m3_anterior >= MINIMO_PARA_COMPARAR_M3 else None
        usuarios.append({
            'id_vivienda': id_vivienda,
            'nic': vivienda.get('nic') or '',
            'usuario': nombre or vivienda.get('nombre_completo_titular') or 'Sin usuario vinculado',
            'correo': dueno.get('correo', ''),
            'vinculada': bool(dueno),
            'direccion': vivienda.get('direccion') or '',
            'zona': zonas.get(id_vivienda) or 'Sin zona',
            'm3': m3,
            'm3_dia': round(m3 / dias, 3),
            'm3_anterior': m3_anterior,
            'cambio': cambio,
            'excede': m3 > limite_periodo,
            'exceso_m3': round(max(0.0, m3 - limite_periodo), 3),
            'anormal': cambio is not None and cambio >= AUMENTO_ANORMAL,
        })
    usuarios.sort(key=lambda u: u['m3'], reverse=True)

    por_zona = defaultdict(list)
    for fila in usuarios:
        por_zona[fila['zona']].append(fila)
    total = sum(u['m3'] for u in usuarios)
    lista_zonas = []
    for nombre, filas in por_zona.items():
        m3_zona = round(sum(f['m3'] for f in filas), 3)
        m3_zona_anterior = round(sum(f['m3_anterior'] for f in filas), 3)
        excedidos = [f for f in filas if f['excede']]
        anormales = [f for f in filas if f['anormal']]
        lista_zonas.append({
            'zona': nombre,
            'viviendas': len(filas),
            'm3': m3_zona,
            'promedio_m3': round(m3_zona / len(filas), 3),
            'porcentaje': round(100 * m3_zona / total, 1) if total else 0,
            'cambio': round((m3_zona - m3_zona_anterior) / m3_zona_anterior, 3)
            if m3_zona_anterior >= MINIMO_PARA_COMPARAR_M3 else None,
            'excedidos': len(excedidos),
            'anormales': len(anormales),
            'afectada': bool(excedidos or anormales),
        })
    lista_zonas.sort(key=lambda z: z['m3'], reverse=True)

    tendencia = []
    for dia in _dias(desde, hasta):
        tendencia.append({'dia': dia.isoformat(), 'etiqueta': f'{dia:%d/%m}',
                          'm3': _m3(sum(actual[i].get(dia, 0.0) for i in actual))})

    resultado = {
        'desde': desde, 'hasta': hasta, 'dias': dias,
        'anterior_desde': anterior_desde, 'anterior_hasta': anterior_hasta,
        'limite_mes': limite_mes, 'limite_periodo': limite_periodo,
        'usuarios': usuarios, 'zonas': lista_zonas, 'tendencia': tendencia,
        'total_m3': round(total, 3),
        'promedio_m3': round(total / len(usuarios), 3) if usuarios else 0,
        'excedidos': sum(1 for u in usuarios if u['excede']),
        'anormales': sum(1 for u in usuarios if u['anormal']),
        'ultima_lectura': timezone.localtime(ultima) if ultima else None,
    }
    resultado['hallazgos'] = hallazgos(resultado)
    cache.set(clave, resultado, CACHE_SEGUNDOS)
    return resultado


def hallazgos(datos):
    """Frases cortas que ayudan a decidir (escenario 7): dónde se concentra el consumo y qué revisar."""
    frases = []
    zonas, usuarios = datos['zonas'], datos['usuarios']
    if not usuarios or not datos['total_m3']:
        return ['No hay consumo registrado en este periodo.']
    if zonas:
        mayor = zonas[0]
        frases.append(f"{mayor['zona']} concentra el {mayor['porcentaje']}% del consumo "
                      f"({mayor['m3']:.2f} m³ en {mayor['viviendas']} viviendas).")
        afectadas = [z['zona'] for z in zonas if z['afectada']]
        if afectadas:
            frases.append('Zonas a revisar: ' + ', '.join(afectadas) + '.')
    if datos['excedidos']:
        frases.append(f"{datos['excedidos']} de {len(usuarios)} viviendas pasan el límite de la comunidad "
                      f"({datos['limite_periodo']:.2f} m³ para estos {datos['dias']} días).")
    else:
        frases.append('Ninguna vivienda pasa el límite de la comunidad en este periodo.')
    if datos['anormales']:
        mayor = max((u for u in usuarios if u['anormal']), key=lambda u: u['cambio'])
        frases.append(f"{datos['anormales']} viviendas subieron mucho su consumo frente al periodo anterior; la mayor "
                      f"es {mayor['usuario']} (+{round(mayor['cambio'] * 100)}%). Puede ser una fuga o un cambio de uso.")
    sin_consumo = [u for u in usuarios if u['m3'] == 0]
    if sin_consumo:
        frases.append(f'{len(sin_consumo)} viviendas no registran consumo: revisa si el sensor de flujo está '
                      'conectado.')
    return frases


def detalle(id_vivienda, desde, hasta, limite_mes=None):
    """Consumo de una sola vivienda día por día (para la pantalla de detalle de un usuario).

    Devuelve None si la vivienda no existe.
    """
    limite_mes = limite_mensual() if limite_mes is None else limite_mes
    filas = supabase_client.select(
        'vivienda', 'id_vivienda,nic,direccion,id_usuario_propietario,nombre_completo_titular,tipo_establecimiento',
        {'id_vivienda': f'eq.{int(id_vivienda)}', 'limit': '1'},
    )
    if not filas:
        return None
    vivienda = filas[0]
    dueno = users_by_id([vivienda.get('id_usuario_propietario')], 'id_usuario,nombre,apellido,correo').get(
        vivienda.get('id_usuario_propietario')) or {}
    por_dia, zonas, ultima = _consumo_por_dia([int(id_vivienda)], desde, hasta)
    dias = _dias(desde, hasta)
    limite_dia = round(limite_mes / DIAS_DEL_MES, 3)
    lista = [{'dia': dia, 'etiqueta': f'{_DIAS_SEMANA[dia.weekday()]} {dia:%d/%m/%Y}',
              'm3': _m3(por_dia[int(id_vivienda)].get(dia, 0.0))} for dia in dias]
    for item in lista:
        item['excede'] = item['m3'] > limite_dia
    total = round(sum(item['m3'] for item in lista), 3)
    con_consumo = [item for item in lista if item['m3'] > 0]
    nombre = f"{dueno.get('nombre', '')} {dueno.get('apellido', '')}".strip()
    return {
        'id_vivienda': int(id_vivienda),
        'usuario': nombre or vivienda.get('nombre_completo_titular') or 'Sin usuario vinculado',
        'correo': dueno.get('correo', ''),
        'vinculada': bool(dueno),
        'nic': vivienda.get('nic') or '',
        'direccion': vivienda.get('direccion') or '',
        'tipo': vivienda.get('tipo_establecimiento') or '',
        'zona': zonas.get(int(id_vivienda)) or 'Sin zona',
        'dias': lista,
        'total_m3': total,
        'promedio_dia_m3': round(total / len(dias), 3) if dias else 0,
        'dia_mayor': max(con_consumo, key=lambda item: item['m3']) if con_consumo else None,
        'dias_sobre_limite': sum(1 for item in lista if item['excede']),
        'limite_dia': limite_dia,
        'limite_periodo': round(limite_mes * len(dias) / DIAS_DEL_MES, 3),
        'excede': total > limite_mes * len(dias) / DIAS_DEL_MES,
        'ultima_lectura': timezone.localtime(ultima) if ultima else None,
    }
