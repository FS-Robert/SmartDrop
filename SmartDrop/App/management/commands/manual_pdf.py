"""Crea el PDF del manual de usuario que se descarga desde la web y la app (PB045).

Uso (con el servidor corriendo):
    python manage.py manual_pdf
    python manage.py manual_pdf --url http://localhost:8000

Abre el manual con Microsoft Edge o Google Chrome sin ventana y lo guarda como PDF en
App/Static/App/manual/SmartDrop-manual-de-usuario.pdf. Hay que correrlo otra vez cada vez que cambie el manual.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DESTINO = Path(settings.BASE_DIR) / 'App' / 'Static' / 'App' / 'manual' / 'SmartDrop-manual-de-usuario.pdf'
NAVEGADORES = (
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'msedge', 'google-chrome', 'chromium', 'chromium-browser',
)


def _navegador():
    for opcion in NAVEGADORES:
        ruta = shutil.which(opcion) or (opcion if Path(opcion).exists() else None)
        if ruta:
            return ruta
    return None


class Command(BaseCommand):
    help = 'Guarda el manual de usuario como PDF para descargarlo sin internet.'

    def add_arguments(self, parser):
        parser.add_argument('--url', default='http://localhost:8000', help='Dirección donde corre SmartDrop.')

    def handle(self, *args, **opciones):
        navegador = _navegador()
        if not navegador:
            raise CommandError('No se encontró Microsoft Edge ni Google Chrome para crear el PDF.')
        # La versión de la app del manual no pide sesión y no tiene menú: es la mejor para imprimir.
        direccion = opciones['url'].rstrip('/') + '/manual/?app=1'
        DESTINO.parent.mkdir(parents=True, exist_ok=True)
        DESTINO.unlink(missing_ok=True)  # así no se confunde con un PDF viejo si algo falla
        with tempfile.TemporaryDirectory() as perfil:
            subprocess.run([
                navegador, '--headless=new', '--disable-gpu', f'--user-data-dir={perfil}',
                '--no-pdf-header-footer', f'--print-to-pdf={DESTINO}', direccion,
            ], check=False, timeout=120, capture_output=True)
        if not DESTINO.exists() or DESTINO.stat().st_size < 10_000:
            raise CommandError(f'No se pudo crear el PDF. ¿Está corriendo el servidor en {opciones["url"]}?')
        self.stdout.write(self.style.SUCCESS(f'PDF listo: {DESTINO} ({DESTINO.stat().st_size // 1024} KB)'))
