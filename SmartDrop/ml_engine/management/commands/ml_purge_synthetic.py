import shutil
from pathlib import Path

from django.core.management.base import BaseCommand

from ml_engine.models import Home, ModelArtifact, SourceType, Zone


class Command(BaseCommand):
    help = (
        'Elimina de la base de ML las zonas/hogares sintéticos (con sus lecturas, agregados y '
        'predicciones) y los modelos entrenados con ellos. Los hogares reales no se tocan.'
    )

    def handle(self, *args, **options):
        homes = Home.objects.filter(source=SourceType.SYNTHETIC).count()
        zones = Zone.objects.filter(source=SourceType.SYNTHETIC)
        zone_count = zones.count()
        zones.delete()
        Home.objects.filter(source=SourceType.SYNTHETIC).delete()

        # Los modelos existentes se entrenaron con datos sintéticos: se descartan para reentrenar con los reales.
        artifacts = list(ModelArtifact.objects.all())
        for artifact in artifacts:
            shutil.rmtree(Path(artifact.file_path), ignore_errors=True)
        ModelArtifact.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f'Eliminados {zone_count} zonas, {homes} hogares sintéticos y {len(artifacts)} modelos.'
        ))
