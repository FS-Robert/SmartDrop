import getpass

from django.core.management.base import BaseCommand, CommandError
from passlib.hash import bcrypt

from App import supabase_client
from App.forms import errores_contrasena


class Command(BaseCommand):
    help = (
        'Cambia la contraseña de un usuario de Supabase (hash bcrypt, igual que el registro). '
        'La contraseña se pide sin mostrarla; no la pases en la línea de comandos.'
    )

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Correo del usuario')
        parser.add_argument(
            'password', type=str, nargs='?',
            help='(No recomendado: queda en el historial de la terminal) contraseña nueva',
        )
        parser.add_argument('--create', action='store_true', help='Crear el usuario (con rol admin) si no existe')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        password = options['password']
        if password:
            self.stdout.write(self.style.WARNING(
                'Aviso: la contraseña escrita en el comando queda en el historial de la terminal. '
                'Omítela para que se pida sin mostrarla.'
            ))
        else:
            password = getpass.getpass('Contraseña nueva: ')
            if password != getpass.getpass('Repítela: '):
                raise CommandError('Las contraseñas no coinciden.')

        errores = errores_contrasena(password, email)
        if errores:
            raise CommandError(' '.join(errores))

        hashed = bcrypt.using(rounds=12, ident='2b').hash(password)
        self.stdout.write(f'Actualizando la contraseña de {email}')
        try:
            row = supabase_client.update(
                'usuario', {'contrasena': hashed}, {'correo': f'eq.{email}'}, return_representation=True,
            )
        except supabase_client.SupabaseError as exc:
            raise CommandError(f'Supabase rechazó el cambio: {exc.detalle}') from exc

        if not row:
            if not options['create']:
                raise CommandError('No existe un usuario con ese correo (usa --create para crearlo).')
            role_id = supabase_client.get_rol_id('admin')
            if role_id is None:
                raise CommandError("No existe el rol 'admin' en la tabla rol.")
            try:
                supabase_client.insert('usuario', {
                    'nombre': 'Admin', 'apellido': 'Admin', 'correo': email,
                    'contrasena': hashed, 'estado_usuario': True, 'id_rol': role_id,
                })
            except supabase_client.SupabaseError as exc:
                raise CommandError(f'No se pudo crear el usuario: {exc.detalle}') from exc
            self.stdout.write(self.style.SUCCESS('Usuario creado en Supabase.'))

        row = supabase_client.get_user_by_email(email)
        if row and bcrypt.verify(password, row.get('contrasena') or ''):
            self.stdout.write(self.style.SUCCESS('Contraseña guardada y verificada.'))
        else:
            raise CommandError('La contraseña no quedó guardada; revisa el registro en el panel de Supabase.')
