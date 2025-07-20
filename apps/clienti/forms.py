from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML, Submit
from crispy_forms.bootstrap import FormActions
from .models import Cliente


class ClienteForm(forms.ModelForm):
    crea_account_online = forms.BooleanField(
        required=False,
        help_text="Crea automaticamente un account per l'accesso all'area clienti"
    )
    
    class Meta:
        model = Cliente
        fields = [
            'tipo', 'email', 'telefono', 'indirizzo', 'cap', 'citta',
            'nome', 'cognome', 'codice_fiscale',
            'ragione_sociale', 'partita_iva', 'codice_sdi', 'pec',
            'consenso_marketing'
        ]
        widgets = {
            'indirizzo': forms.Textarea(attrs={'rows': 2}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Div(
                Field('tipo', onchange="toggleClienteFields()"),
                Field('email'),
                Field('telefono'),
                css_class='col-md-6'
            ),
            Div(
                Field('indirizzo'),
                Field('cap'),
                Field('citta'),
                css_class='col-md-6'
            ),
            Div(
                HTML('<h5 id="dati-privato-title">Dati Privato</h5>'),
                Field('nome'),
                Field('cognome'),
                Field('codice_fiscale'),
                css_id='dati-privato',
                css_class='col-md-6'
            ),
            Div(
                HTML('<h5 id="dati-azienda-title">Dati Azienda</h5>'),
                Field('ragione_sociale'),
                Field('partita_iva'),
                Field('codice_sdi'),
                Field('pec'),
                css_id='dati-azienda',
                css_class='col-md-6'
            ),
            Div(
                Field('consenso_marketing'),
                Field('crea_account_online'),
                css_class='col-12'
            ),
            FormActions(
                Submit('submit', 'Salva Cliente', css_class='btn-primary'),
                css_class='col-12'
            )
        )
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if Cliente.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Esiste già un cliente con questa email')
        return email
    
    def clean_codice_fiscale(self):
        codice_fiscale = self.cleaned_data.get('codice_fiscale')
        if codice_fiscale and len(codice_fiscale) != 16:
            raise forms.ValidationError('Il codice fiscale deve essere di 16 caratteri')
        return codice_fiscale.upper() if codice_fiscale else ''
    
    def clean_partita_iva(self):
        partita_iva = self.cleaned_data.get('partita_iva')
        if partita_iva and len(partita_iva) != 11:
            raise forms.ValidationError('La partita IVA deve essere di 11 cifre')
        return partita_iva
    
    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')
        
        if tipo == 'privato':
            # Campi obbligatori per privati
            if not cleaned_data.get('nome'):
                self.add_error('nome', 'Campo obbligatorio per clienti privati')
            if not cleaned_data.get('cognome'):
                self.add_error('cognome', 'Campo obbligatorio per clienti privati')
            
            # Pulisci campi azienda
            cleaned_data['ragione_sociale'] = ''
            cleaned_data['partita_iva'] = ''
            cleaned_data['codice_sdi'] = ''
            cleaned_data['pec'] = ''
            
        elif tipo == 'azienda':
            # Campi obbligatori per aziende
            if not cleaned_data.get('ragione_sociale'):
                self.add_error('ragione_sociale', 'Campo obbligatorio per aziende')
            if not cleaned_data.get('partita_iva'):
                self.add_error('partita_iva', 'Campo obbligatorio per aziende')
            
            # Pulisci campi privato
            cleaned_data['nome'] = ''
            cleaned_data['cognome'] = ''
            cleaned_data['codice_fiscale'] = ''
        
        return cleaned_data


class ClienteSearchForm(forms.Form):
    search = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Cerca per nome, email, telefono...',
            'class': 'form-control'
        })
    )
    tipo = forms.ChoiceField(
        choices=[('', 'Tutti')] + Cliente.TIPO_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.form_class = 'form-inline'
        self.helper.layout = Layout(
            Div(
                Field('search', css_class='me-2'),
                Field('tipo', css_class='me-2'),
                Submit('submit', 'Cerca', css_class='btn-primary'),
                css_class='d-flex align-items-center'
            )
        )


class ClienteQuickForm(forms.Form):
    """Form veloce per creazione cliente durante ordine"""
    nome = forms.CharField(max_length=100)
    cognome = forms.CharField(max_length=100)
    telefono = forms.CharField(max_length=20)
    email = forms.EmailField(required=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Div(
                Field('nome'),
                Field('cognome'),
                css_class='col-md-6'
            ),
            Div(
                Field('telefono'),
                Field('email'),
                css_class='col-md-6'
            )
        )
    
    def save(self):
        """Crea un nuovo cliente con i dati essenziali"""
        return Cliente.objects.create(
            tipo='privato',
            nome=self.cleaned_data['nome'],
            cognome=self.cleaned_data['cognome'],
            telefono=self.cleaned_data['telefono'],
            email=self.cleaned_data.get('email', ''),
        )