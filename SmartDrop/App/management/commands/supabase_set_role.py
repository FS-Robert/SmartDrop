from django.core.management.base import BaseCommand, CommandError
from App import supabase_client
import requests


class Command(BaseCommand):
    help = 'Set role for a Supabase user (e.g., admin, user) by email'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user to update')
        parser.add_argument('role', type=str, help='Role name to set (e.g., admin, user)')
        parser.add_argument('--create', action='store_true', help='Create the user if not found (with minimal data)')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        role_name = options['role'].strip()
        create_if_missing = options['create']

        self.stdout.write(f'Setting role `{role_name}` for: {email}')

        role_id = supabase_client.get_rol_id(role_name)
        if role_id is None:
            raise CommandError(f'Role "{role_name}" not found in Supabase')

        url = supabase_client._base_url('usuario')
        headers = supabase_client._headers()
        headers['Prefer'] = 'return=representation'
        params = {'correo': f'eq.{email}'}
        payload = {'id_rol': role_id}

        try:
            resp = requests.patch(url, headers=headers, json=payload, params=params, timeout=10)
        except Exception as exc:
            raise CommandError(f'HTTP request failed: {exc}')

        if resp.ok:
            self.stdout.write(self.style.SUCCESS('Role updated (PATCH succeeded)'))
        else:
            self.stdout.write(self.style.WARNING(f'PATCH returned {resp.status_code}: {resp.text}'))
            if create_if_missing:
                self.stdout.write('User not found; attempting to create user with given role...')
                try:
                    payload_create = {
                        'nombre': 'Auto',
                        'apellido': 'Created',
                        'correo': email,
                        'contrasena': '',
                        'estado_usuario': True,
                        'id_rol': role_id,
                    }
                    row = supabase_client.insert('usuario', payload_create)
                    if row:
                        self.stdout.write(self.style.SUCCESS('User created with requested role.'))
                    else:
                        raise CommandError('User creation returned no row')
                except Exception as exc:
                    raise CommandError(f'Failed to create user: {exc}')
            else:
                raise CommandError('No rows updated and --create not provided')

        # Fetch and print the updated row for confirmation
        try:
            row = supabase_client.get_user_by_email(email)
        except Exception as exc:
            raise CommandError(f'Failed to fetch user after update: {exc}')

        if not row:
            raise CommandError('User not found after update')

        self.stdout.write('Supabase record:')
        self.stdout.write(str(row))
        actual_role_id = row.get('id_rol')
        self.stdout.write(f'Actual id_rol: {actual_role_id} (expected {role_id})')
        if actual_role_id == role_id:
            self.stdout.write(self.style.SUCCESS('Role confirmed in Supabase record.'))
        else:
            self.stdout.write(self.style.ERROR('Role mismatch after update; inspect Supabase dashboard.'))