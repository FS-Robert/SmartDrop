from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.hashers import make_password, check_password

from App import supabase_client
import requests


class Command(BaseCommand):
    help = 'Set and verify a Supabase user password (stores Django-compatible hash)'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user to update')
        parser.add_argument('password', type=str, help='New plaintext password to set')
        parser.add_argument('--create', action='store_true', help='Create the user if not found')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        password = options['password']
        create_if_missing = options['create']

        self.stdout.write(f'Updating password for: {email}')

        hashed = make_password(password)

        url = supabase_client._base_url('usuario')
        headers = supabase_client._headers()
        headers['Prefer'] = 'return=representation'
        params = {'correo': f'eq.{email}'}
        payload = {'contrasena': hashed}

        try:
            resp = requests.patch(url, headers=headers, json=payload, params=params, timeout=10)
        except Exception as exc:
            raise CommandError(f'HTTP request failed: {exc}')

        if resp.status_code in (200, 201, 204) and resp.ok:
            self.stdout.write(self.style.SUCCESS('PATCH request succeeded'))
        else:
            # If no rows updated, maybe user doesn't exist
            self.stdout.write(self.style.WARNING(f'PATCH returned {resp.status_code}: {resp.text}'))
            if create_if_missing:
                self.stdout.write('Attempting to create user...')
                try:
                    role_id = supabase_client.get_rol_id('admin')
                    payload_create = {
                        'nombre': 'Admin',
                        'apellido': 'Admin',
                        'correo': email,
                        'contrasena': hashed,
                        'estado_usuario': True,
                    }
                    if role_id is not None:
                        payload_create['id_rol'] = role_id
                    row = supabase_client.insert('usuario', payload_create)
                    if row:
                        self.stdout.write(self.style.SUCCESS('User created in Supabase.'))
                    else:
                        raise CommandError('User creation returned no row')
                except Exception as exc:
                    raise CommandError(f'Failed to create user: {exc}')
            else:
                raise CommandError('No rows updated and --create not provided')

        # Verify
        try:
            row = supabase_client.get_user_by_email(email)
        except Exception as exc:
            raise CommandError(f'Failed to fetch user after update: {exc}')

        if not row:
            raise CommandError('User not found after update')

        stored = row.get('contrasena')
        self.stdout.write(f'stored contrasena repr: {repr(stored)}')
        ok = check_password(password, stored)
        if ok:
            self.stdout.write(self.style.SUCCESS('Password verification OK (check_password returned True)'))
        else:
            # Provide diagnostics
            self.stdout.write(self.style.ERROR('Password verification FAILED (check_password returned False)'))
            self.stdout.write('Possible causes: value truncated, stored in different column, or stored with extra quotes/escaping.')
            self.stdout.write('Inspect the Supabase record manually in the dashboard if needed.')