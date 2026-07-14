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
        # Blocca solo se esiste gia' un ACCOUNT con questa email. Una
        # scheda Cliente senza account (creata in cassa o da prenotazione
        # guest) non blocca: viene collegata in save().
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Email gia registrata. Prova ad accedere.')
        return email

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('password_conferma'):
            raise ValidationError({'password_conferma': 'Le password non coincidono'})

        # Telefono: valido + regola di collegamento all'anagrafica.
        # Se il numero appartiene a una scheda esistente senza account e
        # il nominativo combacia (>= 90%), la registrazione COLLEGA quella
        # scheda (storico/punti preservati) invece di duplicare. Se il
        # nominativo non combacia, o la scheda e' gia' di un altro
        # account, serve l'intervento dell'operatore.
        telefono = (data.get('telefono') or '').strip()
        self._cliente_da_collegare = None
        if telefono and data.get('nome') is not None:
            from apps.clienti.utils import normalizza_telefono, valuta_collegamento_telefono
            if not normalizza_telefono(telefono):
                raise ValidationError({'telefono': 'Numero di telefono non valido: ricontrollalo.'})
            esito, esistente = valuta_collegamento_telefono(
                telefono, data.get('nome'), data.get('cognome'))
            if esito == 'occupato':
                raise ValidationError({'telefono':
                    'Questo numero e\' gia\' collegato a un altro account. '
                    'Se e\' il tuo, chiamaci o scrivici al 379 233 7051 per lo sblocco.'})
            if esito == 'verifica_fallita':
                raise ValidationError({'telefono':
                    'Questo numero risulta gia\' in anagrafica con un altro '
                    'nominativo. Chiamaci o scrivici al 379 233 7051 per lo sblocco.'})
            if esito == 'collega':
                self._cliente_da_collegare = esistente
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

        cliente = getattr(self, '_cliente_da_collegare', None)
        if cliente is None and d.get('email'):
            # Scheda esistente con la stessa email e senza account
            # (es. creata da prenotazione guest): collega anche qui se
            # il nominativo combacia.
            from apps.clienti.utils import somiglianza_nomi, SOGLIA_SOMIGLIANZA_NOMI
            candidata = Cliente.objects.filter(
                email__iexact=d['email'], user__isnull=True,
            ).order_by('pk').first()
            if candidata and somiglianza_nomi(
                    d['nome'], d['cognome'],
                    candidata.nome, candidata.cognome) >= SOGLIA_SOMIGLIANZA_NOMI:
                cliente = candidata

        if cliente is not None:
            # Collega l'account alla scheda esistente e completa i vuoti
            cliente.user = user
            if not cliente.email:
                cliente.email = d['email']
            if not cliente.telefono:
                cliente.telefono = d['telefono']
            if not cliente.nome:
                cliente.nome = d['nome']
            if not cliente.cognome:
                cliente.cognome = d['cognome']
            cliente.save()
        else:
            cliente = Cliente.objects.create(
                user=user,
                tipo='privato',
                nome=d['nome'],
                cognome=d['cognome'],
                email=d['email'],
                telefono=d['telefono'],
            )
        return user, cliente
