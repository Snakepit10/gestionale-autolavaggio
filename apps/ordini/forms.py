from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML, Submit
from crispy_forms.bootstrap import FormActions
from .models import Ordine, Pagamento
from apps.clienti.models import Cliente


class OrdineForm(forms.ModelForm):
    class Meta:
        model = Ordine
        fields = [
            'cliente', 'tipo_consegna', 'ora_consegna_richiesta',
            'metodo_pagamento', 'nota'
        ]
        widgets = {
            'ora_consegna_richiesta': forms.TimeInput(attrs={'type': 'time'}),
            'nota': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        
        # Personalizza queryset cliente
        self.fields['cliente'].queryset = Cliente.objects.all().order_by('nome', 'ragione_sociale')
        self.fields['cliente'].empty_label = "Seleziona cliente (opzionale)"


class PagamentoForm(forms.ModelForm):
    class Meta:
        model = Pagamento
        fields = ['importo', 'metodo', 'riferimento', 'nota']
        widgets = {
            'importo': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'nota': forms.Textarea(attrs={'rows': 2}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Div(
                Field('importo'),
                Field('metodo'),
                css_class='col-md-6'
            ),
            Div(
                Field('riferimento'),
                Field('nota'),
                css_class='col-md-6'
            ),
            FormActions(
                Submit('submit', 'Registra Pagamento', css_class='btn-success'),
                css_class='col-12'
            )
        )


class FiltroOrdiniForm(forms.Form):
    stato = forms.ChoiceField(
        choices=[('', 'Tutti gli stati')] + Ordine.STATO_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    data_da = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    
    data_a = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    
    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.all(),
        required=False,
        empty_label="Tutti i clienti",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'get'
        self.helper.layout = Layout(
            Div(
                Field('stato', css_class='me-2'),
                Field('data_da', css_class='me-2'),
                Field('data_a', css_class='me-2'),
                Field('cliente', css_class='me-2'),
                Submit('submit', 'Filtra', css_class='btn-primary'),
                css_class='d-flex align-items-end'
            )
        )