"""Genera lecturas demo realistas en Supabase para las viviendas indicadas.

Las lecturas sembradas originalmente eran planas (el mismo valor durante todo el mes), así que ningún
modelo podía aprender patrones. Este comando las reemplaza por series con ciclos diarios, balance de
masa coherente con el tanque (consumo -> nivel -> bomba) y escenarios de ejemplo (fugas y fallo de
bomba) en las últimas horas, para validar la predicción de desabasto y la detección de fugas.

Solo toca las viviendas pasadas en --viviendas (ids). Los sensores se comparten entre viviendas: se usa el
sensor de cada tipo y el tanque "Tanque Principal Vivienda <id>". Usa --dry-run para ver qué haría.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from App import queries, supabase_client

STEP_MINUTES = 10
INSERT_BATCH = 1000
INSERT_PARALLEL = 8
DELETE_CHUNK_DAYS = 5


def _sensor_map(sensor_types):
    """{'flujo': id, 'presion': id, 'nivel': id, 'tds': id}: el sensor (compartido) de cada tipo."""
    mapping = {}
    for sensor_id in sorted(sensor_types):
        kind = sensor_types[sensor_id]
        if kind == 'calidad':
            kind = 'tds'
        if kind in ('flujo', 'presion', 'nivel', 'tds'):
            mapping.setdefault(kind, sensor_id)
    return mapping


def _daily_profile(hour_fraction, evening_bias):
    """Perfil de uso doméstico 0..1: picos de mañana y de tarde, casi nulo de madrugada."""
    morning = np.exp(-((hour_fraction - 7.0) ** 2) / 2.0)
    evening = np.exp(-((hour_fraction - 19.0) ** 2) / 3.0) * evening_bias
    midday = 0.35 * np.exp(-((hour_fraction - 13.0) ** 2) / 4.0)
    return morning + evening + midday + 0.01


def simulate_home(vivienda_id, tank, start, steps, scenario, rng, initial_level_cm=None):
    """Devuelve arrays flujo (L/min), presion (bar), nivel (cm), tds (ppm) por paso de 10 min.

    `initial_level_cm` continúa la serie desde un nivel conocido (modo --extend).
    """
    capacity = float(tank['capacidad_maxima_litros'])
    height = float(tank['altura_total'])
    critical_l = capacity * float(tank['nivel_critico_cm']) / height
    peak_flow = rng.uniform(0.12, 0.35)
    evening_bias = rng.uniform(0.8, 1.3)
    base_pressure = rng.uniform(2.0, 3.7)
    base_tds = rng.uniform(90, 340)
    pump_lpm = rng.uniform(1.2, 1.8)

    level = capacity * rng.uniform(0.6, 0.9)
    if initial_level_cm is not None:
        level = float(np.clip(initial_level_cm / height * capacity, 0.0, capacity))
    pump_on = False
    tds = base_tds
    leak_start = steps - scenario['leak_steps'] if scenario['leak'] else None
    pump_fail_start = steps - scenario['pump_fail_steps'] if scenario['pump_fail'] else None
    leak_flow = rng.uniform(0.14, 0.26)

    flow = np.zeros(steps)
    pressure = np.zeros(steps)
    level_cm = np.zeros(steps)
    tds_series = np.zeros(steps)
    for i in range(steps):
        moment = start + timedelta(minutes=STEP_MINUTES * i)
        local_hour = (moment.hour - 6) % 24 + moment.minute / 60.0
        weekday_factor = 1.15 if moment.weekday() >= 5 else 1.0
        use = peak_flow * weekday_factor * _daily_profile(local_hour, evening_bias) * rng.uniform(0.6, 1.3)
        use = max(use * (rng.random() < 0.85), 0.0)

        leaking = leak_start is not None and i >= leak_start
        if leaking:
            ramp = min((i - leak_start) / 6.0, 1.0)
            use += leak_flow * ramp * rng.uniform(0.9, 1.1)

        pump_allowed = not (pump_fail_start is not None and i >= pump_fail_start)
        if level <= capacity * 0.55 and pump_allowed:
            pump_on = True
        if level >= capacity * 0.92 or not pump_allowed:
            pump_on = False

        level = level - use * STEP_MINUTES + (pump_lpm * STEP_MINUTES if pump_on else 0.0)
        level = float(np.clip(level, 0.0, capacity))

        drop = 0.0
        if leaking:
            drop = 0.9 * min((i - leak_start) / 6.0, 1.0)
        elif pump_fail_start is not None and i >= pump_fail_start:
            drop = 0.15
        pressure_value = base_pressure - 0.18 * use / max(peak_flow, 1e-6) - drop + rng.normal(0, 0.03)
        if pump_on:
            pressure_value += 0.12
        tds = 0.97 * tds + 0.03 * base_tds + rng.normal(0, 1.2)

        flow[i] = use
        pressure[i] = max(pressure_value, 0.0)
        level_cm[i] = level / capacity * height
        tds_series[i] = max(tds, 0.0)
    return flow, pressure, level_cm, tds_series, critical_l


class Command(BaseCommand):
    help = 'Reemplaza las lecturas de las viviendas indicadas por series demo realistas (con fugas de ejemplo).'

    def add_arguments(self, parser):
        parser.add_argument('--viviendas', required=True, help='Ids de vivienda separados por coma (p. ej. 9,10,11).')
        parser.add_argument('--days', type=int, default=30)
        parser.add_argument('--leaks', type=int, default=3, help='Viviendas con una fuga activa en las últimas horas.')
        parser.add_argument('--leak-hours', type=int, default=8)
        parser.add_argument('--pump-failures', type=int, default=2, help='Viviendas cuya bomba falla en las últimas horas.')
        parser.add_argument('--pump-fail-hours', type=float, default=1.5)
        parser.add_argument('--seed', type=int, default=2026)
        parser.add_argument('--scenario-seed', type=int, default=None,
                            help='Semilla para elegir qué viviendas tienen fuga/fallo (por defecto, --seed).')
        parser.add_argument('--extend', action='store_true',
                            help='No borra nada: continúa cada vivienda desde su última lectura hasta ahora.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        ids = [part.strip() for part in options['viviendas'].split(',') if part.strip()]
        if not ids or not all(part.isdigit() for part in ids):
            raise CommandError('--viviendas debe ser una lista de ids numéricos separados por coma.')
        viviendas = supabase_client.select(
            'vivienda', 'id_vivienda,nic', {'id_vivienda': f"in.({','.join(ids)})", 'order': 'id_vivienda', 'limit': '200'},
        )
        if not viviendas:
            raise CommandError('No se encontraron esas viviendas en Supabase.')
        sensor_types = {
            row['id_sensor']: str(row['tipo_sensor']).lower()
            for row in queries.sensors()
        }
        tanks = supabase_client.select('tanque', '*', {'limit': '1000'})
        tank_by_name = {str(tank.get('nombre') or '').strip().lower(): tank for tank in tanks}
        mapping = _sensor_map(sensor_types)

        plan = []
        for vivienda in viviendas:
            tank = tank_by_name.get(f"tanque principal vivienda {vivienda['id_vivienda']}")
            if len(mapping) < 4 or tank is None:
                self.stdout.write(self.style.WARNING(f"{vivienda['nic']}: sensores/tanque incompletos, se omite."))
                continue
            plan.append((vivienda, mapping, tank))

        rng = np.random.default_rng(options['scenario_seed'] if options['scenario_seed'] is not None else options['seed'])
        order = rng.permutation(len(plan))
        leak_homes = {int(i) for i in order[:options['leaks']]}
        pump_homes = {int(i) for i in order[options['leaks']:options['leaks'] + options['pump_failures']]}

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % STEP_MINUTES)
        full_steps = options['days'] * 24 * 60 // STEP_MINUTES + 1
        full_start = now - timedelta(minutes=STEP_MINUTES * (full_steps - 1))

        self.stdout.write(
            f"{len(plan)} viviendas, {'continuación hasta' if options['extend'] else f'{full_steps} lecturas por sensor hasta'} "
            f'{now:%Y-%m-%d %H:%M} UTC. Fuga en {len(leak_homes)}, fallo de bomba en {len(pump_homes)}.'
        )
        all_rows = []
        for index, (vivienda, mapping, tank) in enumerate(plan):
            start, steps, initial_level = full_start, full_steps, None
            if options['extend']:
                last = supabase_client.select('lectura', 'valor,fecha_registro', {
                    'id_vivienda': f"eq.{vivienda['id_vivienda']}", 'id_sensor': f"eq.{mapping['nivel']}",
                    'order': 'fecha_registro.desc', 'limit': '1',
                })
                if not last:
                    self.stdout.write(self.style.WARNING(f"{vivienda['nic']}: sin lecturas previas, se omite (usa el modo normal)."))
                    continue
                last_ts = datetime.fromisoformat(last[0]['fecha_registro'].replace('Z', '+00:00'))
                start = last_ts + timedelta(minutes=STEP_MINUTES)
                steps = int((now - start).total_seconds() // (STEP_MINUTES * 60)) + 1
                initial_level = float(last[0]['valor'])
                if steps <= 0:
                    continue
            scenario = {
                'leak': index in leak_homes,
                'leak_steps': options['leak_hours'] * 60 // STEP_MINUTES,
                'pump_fail': index in pump_homes,
                'pump_fail_steps': int(options['pump_fail_hours'] * 60 // STEP_MINUTES),
            }
            home_rng = np.random.default_rng(options['seed'] * 1000 + vivienda['id_vivienda'])
            if options['extend']:
                # Misma semilla que la serie original: conserva la presión, TDS y consumo base de la vivienda.
                scenario['leak_steps'] = min(scenario['leak_steps'], steps)
                scenario['pump_fail_steps'] = min(scenario['pump_fail_steps'], steps)
            flow, pressure, level, tds, _ = simulate_home(
                vivienda['id_vivienda'], tank, start, steps, scenario, home_rng, initial_level_cm=initial_level,
            )
            tag = 'FUGA' if scenario['leak'] else ('FALLO BOMBA' if scenario['pump_fail'] else 'normal')
            self.stdout.write(f"  {vivienda['nic']} ({tag}): flujo medio {flow.mean():.3f} L/min, nivel {level.min():.1f}-{level.max():.1f} cm")
            for i in range(steps):
                ts = (start + timedelta(minutes=STEP_MINUTES * i)).isoformat()
                for kind, series in (('flujo', flow), ('presion', pressure), ('nivel', level), ('tds', tds)):
                    all_rows.append({
                        'id_sensor': mapping[kind],
                        'id_vivienda': vivienda['id_vivienda'],
                        'valor': round(float(series[i]), 4),
                        'fecha_registro': ts,
                    })

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'--dry-run: se escribirían {len(all_rows)} lecturas (no se borró ni insertó nada).'))
            return

        if not options['extend']:
            self._delete_old(plan, now)
        self._insert(all_rows)
        self.stdout.write(self.style.SUCCESS(f'Listo: {len(all_rows)} lecturas demo insertadas.'))

    def _delete_old(self, plan, now):
        self.stdout.write('Borrando lecturas planas anteriores de las viviendas demo...')
        horizon = now + timedelta(days=1)
        floor = now - timedelta(days=45)

        def delete_home(vivienda_id):
            cursor = horizon
            while cursor > floor:
                chunk_start = cursor - timedelta(days=DELETE_CHUNK_DAYS)
                supabase_client.delete('lectura', {
                    'id_vivienda': f'eq.{vivienda_id}',
                    'and': f'(fecha_registro.gte.{chunk_start.isoformat()},fecha_registro.lt.{cursor.isoformat()})',
                })
                cursor = chunk_start

        ids = [vivienda['id_vivienda'] for vivienda, _, _ in plan]
        for i in range(0, len(ids), INSERT_PARALLEL):
            queries.run_parallel(*[(lambda v=v: delete_home(v)) for v in ids[i:i + INSERT_PARALLEL]])
            self.stdout.write(f'  {min(i + INSERT_PARALLEL, len(ids))}/{len(ids)} viviendas borradas')
    def _insert(self, rows):
        batches = [rows[i:i + INSERT_BATCH] for i in range(0, len(rows), INSERT_BATCH)]
        self.stdout.write(f'Insertando {len(rows)} lecturas en {len(batches)} lotes...')
        for i in range(0, len(batches), INSERT_PARALLEL):
            group = batches[i:i + INSERT_PARALLEL]
            queries.run_parallel(*[(lambda b=b: supabase_client.insert_many('lectura', b)) for b in group])
            if (i // INSERT_PARALLEL) % 10 == 0:
                self.stdout.write(f'  {min(i + INSERT_PARALLEL, len(batches))}/{len(batches)} lotes')
