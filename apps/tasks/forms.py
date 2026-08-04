"""Form del modulo task."""
from django import forms

from apps.cq.forms import get_operatori_queryset

from .models import CommentoTask, Etichetta, Progetto, Task


class _OperatoriField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return obj.get_full_name() or obj.username


class TaskForm(forms.ModelForm):
    assegnatari = _OperatoriField(
        queryset=None, required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '5'}))

    class Meta:
        model = Task
        fields = ['titolo', 'descrizione', 'progetto', 'priorita',
                  'scadenza', 'assegnatari', 'etichette', 'visibilita']
        widgets = {
            'titolo': forms.TextInput(attrs={'class': 'form-control'}),
            'descrizione': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'progetto': forms.Select(attrs={'class': 'form-select'}),
            'priorita': forms.Select(attrs={'class': 'form-select'}),
            'scadenza': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'),
            'etichette': forms.SelectMultiple(attrs={'class': 'form-select', 'size': '4'}),
            'visibilita': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assegnatari'].queryset = get_operatori_queryset()
        self.fields['progetto'].queryset = Progetto.objects.filter(archiviato=False)
        self.fields['scadenza'].input_formats = ['%Y-%m-%dT%H:%M']


class QuickAddForm(forms.Form):
    """Aggiunta rapida stile Todoist: solo titolo (+ progetto implicito
    quando si e' dentro la vista di un progetto)."""
    titolo = forms.CharField(max_length=200)
    progetto = forms.ModelChoiceField(
        queryset=Progetto.objects.filter(archiviato=False), required=False)


class ProgettoForm(forms.ModelForm):
    class Meta:
        model = Progetto
        fields = ['nome', 'colore', 'ordine']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'colore': forms.TextInput(attrs={'class': 'form-control form-control-color',
                                             'type': 'color'}),
            'ordine': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class EtichettaForm(forms.ModelForm):
    class Meta:
        model = Etichetta
        fields = ['nome', 'colore']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'colore': forms.TextInput(attrs={'class': 'form-control form-control-color',
                                             'type': 'color'}),
        }


class CommentoForm(forms.ModelForm):
    class Meta:
        model = CommentoTask
        fields = ['testo']
        widgets = {
            'testo': forms.Textarea(attrs={'class': 'form-control', 'rows': 2,
                                           'placeholder': 'Scrivi un commento...'}),
        }
