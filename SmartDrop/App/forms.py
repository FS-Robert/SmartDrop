from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import translation

from . import seguridad, supabase_client
from .backends import sync_user_from_supabase
from .models import Usuario

# LANGUAGE_CODE es 'en-us' (no se cambia para no alterar el formato de números y fechas): los formularios de
# acceso definen sus propios mensajes para mostrarse en español.
ERRORES_CORREO = {'required': 'El correo es obligatorio.', 'invalid': 'Escribe un correo electrónico válido.'}


def errores_contrasena(password, email='', nombre='', apellido=''):
    """Mensajes (en español) de las reglas de AUTH_PASSWORD_VALIDATORS que la contraseña no cumple.

    bcrypt solo usa los primeros 72 bytes: una contraseña más larga se rechaza en vez de recortarse en silencio.
    """
    if len(password.encode('utf-8')) > 72:
        return ['La contraseña es demasiado larga (máximo 72 caracteres).']
    with translation.override('es'):
        try:
            validate_password(password, user=Usuario(email=email, nombre=nombre, apellido=apellido))
        except ValidationError as exc:
            return list(exc.messages)
    return []


class UsuarioProfileForm(forms.Form):
    nombre = forms.CharField(
        label='Nombre',
        max_length=255,
        widget=forms.TextInput(attrs={'autocomplete': 'given-name'}),
    )
    apellido = forms.CharField(
        label='Apellido',
        max_length=255,
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    email = forms.EmailField(
        label='Correo electrónico',
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class VincularViviendaForm(forms.Form):
    numero_cuenta = forms.CharField(
        label='Número de cuenta (NIC)',
        max_length=64,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Número de cuenta (NIC)'}),
    )
    nombre_completo_titular = forms.CharField(
        label='Nombre completo del titular',
        max_length=255,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Nombre completo del titular'}),
    )


class UsuarioRegisterForm(forms.Form):
    email = forms.EmailField(
        label='Correo', max_length=254, error_messages=ERRORES_CORREO,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'nombre@correo.com'}),
    )
    nombre = forms.CharField(
        label='Nombre', max_length=255, error_messages={'required': 'El nombre es obligatorio.'},
        widget=forms.TextInput(attrs={'autocomplete': 'given-name', 'autofocus': True}),
    )
    apellido = forms.CharField(
        label='Apellido', max_length=255, error_messages={'required': 'El apellido es obligatorio.'},
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    password1 = forms.CharField(
        label='Contraseña', error_messages={'required': 'La contraseña es obligatoria.'},
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirmar contraseña', error_messages={'required': 'Confirma tu contraseña.'},
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Las contraseñas no coinciden')
        return password2

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password1')
        if password:
            errores = errores_contrasena(
                password, cleaned_data.get('email') or '', cleaned_data.get('nombre') or '', cleaned_data.get('apellido') or '',
            )
            if errores:
                self.add_error('password1', errores)
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip().lower()
            try:
                if supabase_client.email_exists(email):
                    raise forms.ValidationError('Este correo ya está registrado')
            except supabase_client.SupabaseError as exc:
                raise forms.ValidationError(
                    'No se pudo verificar el correo. Inténtalo de nuevo en unos minutos.'
                ) from exc
        return email

    def save(self):
        row = supabase_client.create_usuario(
            nombre=self.cleaned_data['nombre'],
            apellido=self.cleaned_data['apellido'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password1'],
        )
        return sync_user_from_supabase(row)


class LoginForm(forms.Form):
    email = forms.EmailField(
        label='Correo', error_messages=ERRORES_CORREO,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'nombre@correo.com', 'autofocus': True}),
    )
    password = forms.CharField(
        label='Contraseña', error_messages={'required': 'La contraseña es obligatoria.'},
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        password = cleaned_data.get('password')
        if email and password:
            espera = seguridad.acceso_bloqueado(email)
            if espera:
                raise forms.ValidationError(seguridad.mensaje_bloqueo(espera))
            try:
                user = authenticate(username=email, password=password)
            except supabase_client.SupabaseError as exc:
                raise forms.ValidationError(
                    'No se pudo validar el inicio de sesión. Inténtalo de nuevo en unos minutos.'
                ) from exc
            if user is None:
                seguridad.registrar_fallo_acceso(email)
                raise forms.ValidationError('Correo o contraseña incorrectos')
            cleaned_data['user'] = user
        return cleaned_data
