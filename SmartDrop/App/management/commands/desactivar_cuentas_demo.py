import secrets

from django.core.management.base import BaseCommand
from passlib.hash import bcrypt

from App import supabase_client

# Las 25 cuentas de supabase/seed_demo_realista.sql (ya borrado) comparten una contraseña que quedó en el
# historial de Git: cualquiera con acceso al repositorio podría entrar con ellas.
PATRON_DEMO = 'demo*@smartdrop.test'


class Command(BaseCommand):
    help = (
        'Desactiva las cuentas demo01…demo25@smartdrop.test y les pone una contraseña aleatoria. '
        'Sin --aplicar solo muestra cuáles encontró.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Desactivarlas de verdad')

    def handle(self, *args, **options):
        cuentas = supabase_client.select(
            'usuario', 'id_usuario,correo,estado_usuario', {'correo': f'like.{PATRON_DEMO}', 'limit': '200'},
        )
        if not cuentas:
            self.stdout.write(self.style.SUCCESS('No hay cuentas demo en Supabase.'))
            return
        for cuenta in cuentas:
            estado = 'activa' if cuenta.get('estado_usuario') else 'ya desactivada'
            self.stdout.write(f"  {cuenta['correo']} ({estado})")
        if not options['aplicar']:
            self.stdout.write(self.style.WARNING(
                f'{len(cuentas)} cuentas demo. Vuelve a ejecutar con --aplicar para desactivarlas.'
            ))
            return
        for cuenta in cuentas:
            supabase_client.update(
                'usuario',
                {
                    'estado_usuario': False,
                    'contrasena': bcrypt.using(rounds=12, ident='2b').hash(secrets.token_urlsafe(32)),
                },
                {'id_usuario': f"eq.{cuenta['id_usuario']}"},
            )
        self.stdout.write(self.style.SUCCESS(
            f'{len(cuentas)} cuentas demo desactivadas. Sus sesiones abiertas se cierran en unos minutos.'
        ))
