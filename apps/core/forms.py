from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML
from .models import Categoria, ServizioProdotto, Sconto, StampanteRete


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ['nome', 'descrizione', 'ordine_visualizzazione', 'attiva']
        widgets = {
            'descrizione': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_class = 'form-horizontal'


class ServizioProdottoForm(forms.ModelForm):
    class Meta:
        model = ServizioProdotto
        fields = [
            'titolo', 'tipo', 'categoria', 'prezzo', 'descrizione',
            'durata_minuti', 'postazioni', 'quantita_disponibile',
            'quantita_minima_alert', 'codice_prodotto', 'attivo'
        ]
        widgets = {
            'descrizione': forms.Textarea(attrs={'rows': 3}),
            'postazioni': forms.CheckboxSelectMultiple(),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Div(
                Field('titolo'),
                Field('tipo', onchange="toggleFields()"),
                Field('categoria'),
                Field('prezzo'),
                css_class='col-md-6'
            ),
            Div(
                Field('descrizione'),
                Field('attivo'),
                css_class='col-md-6'
            ),
            Div(
                HTML('<h5>Campi per Servizi</h5>'),
                Field('durata_minuti'),
                Field('postazioni'),
                css_id='servizio-fields'
            ),
            Div(
                HTML('<h5>Campi per Prodotti</h5>'),
                Field('quantita_disponibile'),
                Field('quantita_minima_alert'),
                Field('codice_prodotto'),
                css_id='prodotto-fields'
            )
        )
    
    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')
        
        if tipo == 'servizio':
            cleaned_data['quantita_disponibile'] = -1
            cleaned_data['quantita_minima_alert'] = 0
            cleaned_data['codice_prodotto'] = ''
        elif tipo == 'prodotto':
            cleaned_data['durata_minuti'] = 0
            # Rimuovi le postazioni per i prodotti
            if 'postazioni' in cleaned_data:
                cleaned_data['postazioni'] = []
        
        return cleaned_data


class ScontoForm(forms.ModelForm):
    class Meta:
        model = Sconto
        fields = ['titolo', 'tipo_sconto', 'valore', 'attivo']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
    
    def clean_valore(self):
        valore = self.cleaned_data.get('valore')
        tipo_sconto = self.cleaned_data.get('tipo_sconto')
        
        if tipo_sconto == 'percentuale' and valore > 100:
            raise forms.ValidationError("La percentuale non può essere superiore a 100%")
        
        if valore <= 0:
            raise forms.ValidationError("Il valore deve essere positivo")
        
        return valore


class StampanteReteForm(forms.ModelForm):
    class Meta:
        model = StampanteRete
        fields = [
            'nome', 'tipo', 'indirizzo_ip', 'porta', 'modello',
            'larghezza_carta', 'attiva', 'predefinita'
        ]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'