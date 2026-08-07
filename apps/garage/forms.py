"""Form del Garage cliente."""
from django import forms

from .models import Veicolo, normalizza_targa


class VeicoloForm(forms.ModelForm):
    class Meta:
        model = Veicolo
        fields = ['targa', 'marca', 'modello']
        widgets = {
            'targa': forms.TextInput(attrs={
                'class': 'form-control text-uppercase',
                'placeholder': 'AB123CD', 'maxlength': '10'}),
            'marca': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'es. Fiat'}),
            'modello': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'es. Panda'}),
        }

    def __init__(self, *args, cliente=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = cliente

    def clean_targa(self):
        # Stessa normalizzazione/lunghezza del modulo abbonamenti
        targa = normalizza_targa(self.cleaned_data.get('targa', ''))
        if not (5 <= len(targa) <= 8):
            raise forms.ValidationError(
                'Targa non valida: controlla di averla scritta per intero.')
        if self.cliente is not None and Veicolo.objects.filter(
                cliente=self.cliente, targa=targa).exists():
            raise forms.ValidationError(
                'Hai gia\' registrato un veicolo con questa targa.')
        return targa
