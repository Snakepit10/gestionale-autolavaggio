from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML, Submit, Fieldset
from crispy_forms.bootstrap import FormActions
from .models import (
    ConfigurazioneAbbonamento, Abbonamento, AccessoAbbonamento,
    ServizioInclusoAbbonamento
)
from apps.core.models import ServizioProdotto
from apps.clienti.models import Cliente


class ConfigurazioneAbbonamentoForm(forms.ModelForm):
    class Meta:
        model = ConfigurazioneAbbonamento
        fields = [
            'titolo', 'descrizione', 'prezzo', 'attiva',
            'modalita_targa', 'numero_massimo_targhe',
            'periodicita_reset', 'durata', 'giorni_durata',
            'rinnovo_automatico', 'giorni_preavviso_scadenza'
        ]
        widgets = {
            'descrizione': forms.Textarea(attrs={'rows': 3}),
            'prezzo': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'


class AbbonamentoForm(forms.ModelForm):
    class Meta:
        model = Abbonamento
        fields = ['cliente', 'configurazione']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        
        # Personalizza queryset
        self.fields['configurazione'].queryset = ConfigurazioneAbbonamento.objects.filter(attiva=True)


class AccessoAbbonamentoForm(forms.ModelForm):
    class Meta:
        model = AccessoAbbonamento
        fields = ['servizio', 'targa_utilizzata', 'metodo_verifica']
    
    def __init__(self, *args, **kwargs):
        abbonamento = kwargs.pop('abbonamento', None)
        super().__init__(*args, **kwargs)
        
        if abbonamento:
            # Limita i servizi a quelli inclusi nell'abbonamento
            servizi_inclusi = abbonamento.configurazione.servizi_inclusi.values_list('servizio', flat=True)
            self.fields['servizio'].queryset = ServizioProdotto.objects.filter(id__in=servizi_inclusi)


# Forms per Wizard Configurazione Abbonamento

class WizardConfigurazioneBaseForm(forms.Form):
    """Step 1: Informazioni base"""
    titolo = forms.CharField(
        max_length=200,
        help_text="Nome dell'abbonamento (es. 'Abbonamento Premium Mensile')"
    )
    descrizione = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text="Descrizione dettagliata dell'abbonamento"
    )
    prezzo = forms.DecimalField(
        max_digits=10, 
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        help_text="Prezzo dell'abbonamento in euro"
    )
    durata = forms.ChoiceField(
        choices=ConfigurazioneAbbonamento.DURATA_CHOICES,
        help_text="Durata dell'abbonamento"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False


class WizardConfigurazioneTargaForm(forms.Form):
    """Step 2: Modalità targa"""
    modalita_targa = forms.ChoiceField(
        choices=ConfigurazioneAbbonamento.MODALITA_TARGA_CHOICES,
        widget=forms.RadioSelect,
        help_text="Come gestire le targhe per questo abbonamento"
    )
    numero_massimo_targhe = forms.IntegerField(
        min_value=1,
        max_value=10,
        initial=1,
        help_text="Numero massimo di targhe associabili (solo per targhe multiple)"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
    
    def clean(self):
        cleaned_data = super().clean()
        modalita = cleaned_data.get('modalita_targa')
        
        if modalita == 'singola':
            cleaned_data['numero_massimo_targhe'] = 1
        elif modalita == 'libera':
            cleaned_data['numero_massimo_targhe'] = 0
        
        return cleaned_data


class WizardConfigurazioneAccessiForm(forms.Form):
    """Step 3: Frequenza accessi"""
    periodicita_reset = forms.ChoiceField(
        choices=ConfigurazioneAbbonamento.PERIODICITA_CHOICES,
        help_text="Ogni quanto si azzerano i contatori degli accessi"
    )
    rinnovo_automatico = forms.BooleanField(
        required=False,
        help_text="Rinnova automaticamente l'abbonamento alla scadenza"
    )
    giorni_preavviso_scadenza = forms.IntegerField(
        min_value=1,
        max_value=30,
        initial=7,
        help_text="Giorni prima della scadenza per inviare promemoria"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False


class WizardConfigurazioneServiziForm(forms.Form):
    """Step 4: Servizi inclusi"""
    
    def __init__(self, *args, **kwargs):
        servizi = kwargs.pop('servizi', ServizioProdotto.objects.filter(tipo='servizio', attivo=True))
        super().__init__(*args, **kwargs)
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        
        # Crea campi dinamici per ogni servizio
        for servizio in servizi:
            # Checkbox per includere il servizio
            self.fields[f'servizio_{servizio.id}_incluso'] = forms.BooleanField(
                required=False,
                label=f"Includi {servizio.titolo}",
                help_text=f"Durata: {servizio.durata_minuti} min - Prezzo: €{servizio.prezzo}"
            )
            
            # Campo quantità
            self.fields[f'servizio_{servizio.id}_quantita'] = forms.IntegerField(
                min_value=1,
                max_value=100,
                initial=1,
                required=False,
                label="Quantità per periodo",
                widget=forms.NumberInput(attrs={
                    'class': 'form-control form-control-sm',
                    'style': 'width: 80px; display: inline-block;'
                })
            )
    
    def clean(self):
        cleaned_data = super().clean()
        servizi_inclusi = []
        
        # Valida che almeno un servizio sia selezionato
        servizi_selezionati = False
        
        for field_name, value in cleaned_data.items():
            if field_name.endswith('_incluso') and value:
                servizi_selezionati = True
                servizio_id = field_name.split('_')[1]
                quantita_field = f'servizio_{servizio_id}_quantita'
                quantita = cleaned_data.get(quantita_field, 1)
                
                if quantita < 1:
                    raise forms.ValidationError(f'La quantità deve essere almeno 1 per il servizio selezionato')
                
                servizi_inclusi.append({
                    'servizio_id': int(servizio_id),
                    'quantita': quantita
                })
        
        if not servizi_selezionati:
            raise forms.ValidationError('Seleziona almeno un servizio per l\'abbonamento')
        
        cleaned_data['servizi_inclusi'] = servizi_inclusi
        return cleaned_data


class VenditaAbbonamentoForm(forms.Form):
    """Form per vendita nuovo abbonamento"""
    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.all(),
        empty_label="Seleziona cliente",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    configurazione = forms.ModelChoiceField(
        queryset=ConfigurazioneAbbonamento.objects.filter(attiva=True),
        empty_label="Seleziona tipo abbonamento",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    metodo_pagamento = forms.ChoiceField(
        choices=[
            ('contanti', 'Contanti'),
            ('carta', 'Carta'),
            ('bonifico', 'Bonifico'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    importo_pagamento = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'step': '0.01',
            'min': '0',
            'class': 'form-control'
        }),
        help_text="Importo pagato ora (può essere parziale)"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Fieldset(
                'Dati Abbonamento',
                Field('cliente'),
                Field('configurazione', onchange='aggiornaPrezzo()'),
            ),
            Fieldset(
                'Pagamento',
                Field('metodo_pagamento'),
                Field('importo_pagamento'),
            ),
            HTML('<div id="dettagli-configurazione" class="mt-3"></div>'),
            HTML('<div id="sezione-targhe" class="mt-3 d-none"></div>'),
            FormActions(
                Submit('submit', 'Crea Abbonamento', css_class='btn-success'),
                css_class='mt-3'
            )
        )


class VerificaAccessoForm(forms.Form):
    """Form per verifica accesso abbonamento"""
    codice_nfc = forms.CharField(
        max_length=32,
        widget=forms.TextInput(attrs={
            'placeholder': 'Inserisci codice o usa lettore NFC',
            'class': 'form-control form-control-lg',
            'autofocus': True
        }),
        help_text="Inserisci il codice manualmente o usa il lettore NFC"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.form_action = ''
        self.helper.layout = Layout(
            Field('codice_nfc'),
            Submit('submit', 'Verifica Abbonamento', css_class='btn-primary btn-lg w-100 mt-3')
        )


class TargaAbbonamentoForm(forms.Form):
    """Form per inserimento targhe durante registrazione accesso"""
    targa = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'placeholder': 'Es. AB123CD',
            'class': 'form-control text-uppercase',
            'pattern': '[A-Z0-9]+',
            'style': 'text-transform: uppercase;'
        }),
        help_text="Inserisci la targa del veicolo"
    )
    
    def clean_targa(self):
        targa = self.cleaned_data.get('targa', '').upper().replace(' ', '')
        
        if len(targa) < 5 or len(targa) > 8:
            raise forms.ValidationError('La targa deve essere tra 5 e 8 caratteri')
        
        return targa