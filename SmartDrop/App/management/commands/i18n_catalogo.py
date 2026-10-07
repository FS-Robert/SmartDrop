"""Valida el catálogo de traducción y, opcionalmente, lo copia a la app Android.

    python manage.py i18n_catalogo                                   # solo valida
    python manage.py i18n_catalogo --app C:/ruta/SmartDrop-App       # valida y copia a assets/i18n_en.json

El catálogo vive en App/i18n/en.json (frases exactas y plantillas con huecos {0}, {1}…). Después de
agregar textos nuevos a la interfaz, agrega aquí su traducción y vuelve a copiarlo a la app.
"""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from App.i18n.validacion import errores_catalogo

CATALOGO = Path(__file__).resolve().parents[2] / 'i18n' / 'en.json'
ASSET = Path('app') / 'src' / 'main' / 'assets' / 'i18n_en.json'


class Command(BaseCommand):
    help = 'Valida App/i18n/en.json, actualiza su versión y lo copia a la app Android (--app).'

    def add_arguments(self, parser):
        parser.add_argument('--app', help='Carpeta del proyecto Android (SmartDrop-App).')

    def handle(self, *args, **options):
        catalogo = json.loads(CATALOGO.read_text(encoding='utf-8'))
        errores = errores_catalogo(catalogo)
        if errores:
            raise CommandError('El catálogo tiene problemas:\n- ' + '\n- '.join(errores))

        contenido = {'frases': catalogo.get('frases', {}), 'plantillas': catalogo.get('plantillas', {})}
        firma = json.dumps(contenido, ensure_ascii=False, sort_keys=True).encode('utf-8')
        catalogo = {'version': int(hashlib.sha1(firma).hexdigest()[:8], 16), **contenido}
        texto = json.dumps(catalogo, ensure_ascii=False, indent=1) + '\n'
        CATALOGO.write_text(texto, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f"Catálogo válido: {len(contenido['frases'])} frases, {len(contenido['plantillas'])} plantillas "
            f"(versión {catalogo['version']})."))

        if options['app']:
            destino = Path(options['app']) / ASSET
            if not destino.parent.is_dir():
                raise CommandError(f'No existe {destino.parent}; revisa la ruta de la app.')
            destino.write_text(texto, encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'Copiado a {destino}'))
