"""Form lato cliente: registrazione + prenotazione."""
from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.clienti.models import Cliente


class RegistrazioneClienteForm(forms.Form):
    """Registrazione cliente privato. Crea User + Cliente collegato."""

    nome = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Mario',
            'autocomplete': 'given-name',
        }),
    )
    cognome = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Rossi',
            'autocomplete': 'family-name',
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'mario.rossi@email.it',
            'autocomplete': 'email',
            'inputmode': 'email',
        }),
    )
    telefono = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': '333 1234567',
            'autocomplete': 'tel',
            'inputmode': 'tel',
        }),
    )
    password = forms.CharField(
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Minimo 6 caratteri',
            'autocomplete': 'new-password',
        }),
    )
    password_conferma = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ripeti la password',
            'autocomplete': 'new-password',
        }),
    )
    accetta_privacy = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if User.objects.filter(email__iexact=email).exists() or \
           Cliente.objects.filter(email__iexact=email).exists():
            raise ValidationError('Email gia registrata. Prova ad accedere.')
        return email

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('password_conferma'):
            raise ValidationError({'password_conferma': 'Le password non coincidono'})
        return data

    def save(self):
        d = self.cleaned_data
        username = d['email']  # email come username
        user = User.objects.create_user(
            username=username,
            email=d['email'],
            first_name=d['nome'],
            last_name=d['cognome'],
            password=d['password'],
        )
        cliente = Cliente.objects.create(
            user=user,
            tipo='privato',
            nome=d['nome'],
            cognome=d['cognome'],
            email=d['email'],
            telefono=d['telefono'],
        )
        return user, cliente
