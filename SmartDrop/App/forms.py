from django import forms
from django.contrib.auth import authenticate

from . import supabase_client
from .backends import sync_user_from_supabase


class UsuarioRegisterForm(forms.Form):
    email = forms.EmailField(label='Correo')
    nombre = forms.CharField(label='Nombre', max_length=255)
    apellido = forms.CharField(label='Apellido', max_length=255)
    password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Las contraseñas no coinciden')
        return password2

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip().lower()
            try:
                if supabase_client.email_exists(email):
                    raise forms.ValidationError('Este correo ya está registrado')
            except supabase_client.SupabaseError as exc:
                raise forms.ValidationError(f'No se pudo verificar el correo: {exc}') from exc
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
    email = forms.EmailField(label='Correo')
    password = forms.CharField(label='Contraseña', widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        password = cleaned_data.get('password')
        if email and password:
            user = authenticate(username=email, password=password)
            if user is None:
                raise forms.ValidationError('Correo o contraseña incorrectos')
            cleaned_data['user'] = user
        return cleaned_data
