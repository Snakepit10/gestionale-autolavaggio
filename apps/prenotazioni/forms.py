from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML, Submit
from crispy_forms.bootstrap import FormActions
from .models import ConfigurazioneSlot, Prenotazione
from apps.core.models import ServizioProdotto


class ConfigurazioneSlotForm(forms.ModelForm):
    class Meta:
        model = ConfigurazioneSlot
        fields = [
            'giorno_settimana', 'ora_inizio', 'ora_fine',
            'durata_slot_minuti', 'max_prenotazioni_per_slot',
            'servizi_ammessi', 'attivo'
        ]
        widgets = {
            'ora_inizio': forms.TimeInput(attrs={'type': 'time'}),
            'ora_fine': forms.TimeInput(attrs={'type': 'time'}),
            'servizi_ammessi': forms.CheckboxSelectMultiple(),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        
        # Limita servizi solo a quelli attivi
        self.fields['servizi_ammessi'].queryset = ServizioProdotto.objects.filter(
            tipo='servizio', 
            attivo=True
        )


class PrenotazioneForm(forms.ModelForm):
    # Campi aggiuntivi per gestire cliente non registrato
    cliente = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="-- Seleziona cliente registrato --",
        help_text="Lascia vuoto per cliente non registrato"
    )
    nome_cliente = forms.CharField(
        max_length=100,
        required=False,
        help_text="Nome completo (obbligatorio se non è cliente registrato)"
    )
    telefono_cliente = forms.CharField(
        max_length=20,
        required=False,
        help_text="Telefono (opzionale)"
    )
    tipo_auto = forms.CharField(
        max_length=200,
        required=False,
        help_text="Modello e colore dell'auto (es. Fiat 500 Bianca)"
    )
    
    # Campi per la prenotazione
    data_prenotazione = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'min': ''}),
        help_text="Seleziona la data per la prenotazione"
    )
    ora_prenotazione = forms.TimeField(
        widget=forms.TimeInput(attrs={'type': 'time'}),
        help_text="Seleziona l'orario"
    )
    
    
    # Servizi selezionati
    servizi_selezionati = forms.ModelMultipleChoiceField(
        queryset=None,
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        help_text="Seleziona almeno un servizio"
    )
    
    # Durata totale stimata
    durata_stimata_minuti = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Durata totale calcolata automaticamente"
    )
    
    
    # Note
    note_cliente = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Note aggiuntive per la prenotazione"
    )
    
    # Stato (solo in modifica)
    stato = forms.ChoiceField(
        choices=Prenotazione.STATO_CHOICES,
        required=False
    )
    
    class Meta:
        model = Prenotazione
        fields = []  # Gestiremo manualmente i campi
    
    def __init__(self, *args, **kwargs):
        self.cliente_user = kwargs.pop('cliente', None)
        super().__init__(*args, **kwargs)
        
        # Imposta querysets
        from apps.clienti.models import Cliente
        from apps.core.models import ServizioProdotto, Postazione
        
        self.fields['cliente'].queryset = Cliente.objects.all().order_by('nome', 'ragione_sociale')
        self.fields['servizi_selezionati'].queryset = ServizioProdotto.objects.filter(
            tipo='servizio',
            attivo=True
        )
        
        # Imposta data minima a oggi
        from datetime import date
        self.fields['data_prenotazione'].widget.attrs['min'] = date.today().isoformat()
        
        # Se abbiamo un'istanza esistente, popoliamo i campi aggiuntivi
        if self.instance.pk:
            if hasattr(self.instance, 'cliente') and self.instance.cliente:
                self.initial['cliente'] = self.instance.cliente
            # Altri campi andrebbero popolati dal modello esteso
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_class = 'form-horizontal'
    
    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get('cliente')
        nome_cliente = cleaned_data.get('nome_cliente')
        telefono_cliente = cleaned_data.get('telefono_cliente')
        tipo_auto = cleaned_data.get('tipo_auto')
        servizi_selezionati = cleaned_data.get('servizi_selezionati')
        
        # Validazione cliente o dati ospite
        if not cliente:
            if not nome_cliente:
                raise forms.ValidationError(
                    'Se non selezioni un cliente registrato, devi fornire almeno il nome.'
                )
        
        # Validazione servizi
        if not servizi_selezionati:
            raise forms.ValidationError('Seleziona almeno un servizio')
        
        return cleaned_data
    
    def save(self, commit=True):
        # Per ora gestiamo solo il salvataggio di base
        # La logica completa andrà implementata nelle views
        return super().save(commit=False)