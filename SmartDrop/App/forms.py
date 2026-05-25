from django import forms
from django.contrib.auth import authenticate

from .models import Rol, Usuario


class UsuarioRegisterForm(forms.ModelForm):
    password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    class Meta:
        model = Usuario
        fields = ['email', 'nombre', 'apellido']

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Las contraseñas no coinciden')
        return password2

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and Usuario.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Este correo ya está registrado')
        return Usuario.objects.normalize_email(email)

    def clean_rol(self):
        rol_value = self.cleaned_data.get('rol')
        return rol_value if rol_value in ('user', 'admin') else 'user'

    def save(self, commit=True):
        user = super().save(commit=False)
        # Force normal user role on registration
        rol_obj, _ = Rol.objects.get_or_create(
            nombre_rol='user',
            defaults={'descripcion': 'Usuario estándar'},
        )
        user.rol = rol_obj
        user.is_staff = False
        user.is_superuser = False
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


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
