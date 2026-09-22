from django.core.management.base import BaseCommand

from ml_engine.simulation.config import (
    AnomalyInjectionConfig,
    InflowScheduleConfig,
    SimulationConfig,
)
from ml_engine.simulation.generate import generate_dataset


class Command(BaseCommand):
    help = 'Genera un dataset sintético completo (hogares + zonas + tanque) para validar el pipeline (Fase 1).'

    def add_arguments(self, parser):
        parser.add_argument('--zones', type=int, default=2)
        parser.add_argument('--homes-per-zone', type=int, default=15)
        parser.add_argument('--days', type=int, default=60)
        parser.add_argument('--start-date', type=str, default='2026-01-01')
        parser.add_argument('--seed', type=int, default=42)

    def handle(self, *args, **options):
        config = SimulationConfig(
            n_zones=options['zones'],
            homes_per_zone=options['homes_per_zone'],
            start_date=options['start_date'],
            days=options['days'],
            seed=options['seed'],
            anomalies=AnomalyInjectionConfig(),
            inflow=InflowScheduleConfig(),
        )
        self.stdout.write(f'Generando dataset sintético: {config}')
        summary = generate_dataset(config)
        self.stdout.write(self.style.SUCCESS(
            f"Listo: {summary['zones']} zonas, {summary['homes']} hogares, "
            f"{summary['readings']} lecturas, {summary['aggregates']} agregados horarios."
        ))
