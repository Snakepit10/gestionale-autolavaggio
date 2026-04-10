from django import forms
from django.contrib.auth.models import User
from django.forms import modelformset_factory, BaseModelFormSet

from apps.cq.models import (
    SchedaCQ, DifettoCQ, OperatorePostazioneTurno, ModificaPunteggio,
    ImpostazionePremioMensile, Postazione, AzioneCorrettiva, Gravita,
)


def get_operatori_queryset():
    """Operatori attivi: staff o appartenenti a un gruppo CQ."""
    return User.objects.filter(
        is_active=True
    ).filter(
        groups__name__in=['titolare', 'responsabile', 'operatore']
    ).distinct().order_by('first_name', 'last_name', 'username')


class OperatoriTurnoForm(forms.Form):
    """
    Sottosezione del form scheda CQ per registrare chi ha lavorato a ogni postazione.
    Un form per postazione — supporta più operatori per la stessa postazione.
    """
    postazione = forms.ChoiceField(choices=Postazione.choices, widget=forms.HiddenInput)
    operatori = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        label='Operatori',
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operatori'].queryset = get_operatori_queryset()


class SchedaCQForm(forms.ModelForm):
    class Meta:
        model = SchedaCQ
        fields = ['esito', 'note']
        widgets = {
            'esito': forms.RadioSelect(),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['esito'].label = 'Esito controllo'
        self.fields['note'].required = False
        # Django prepend a blank choice to RadioSelect by default — rimuoviamola
        from apps.cq.models import EsitoCQ
        self.fields['esito'].choices = list(EsitoCQ.choices)


class DifettoCQForm(forms.ModelForm):
    """
    Usato solo per la validazione server-side dei dati inviati dal form CQ.
    La selezione zona/tipo avviene tramite UI POS in JS; i valori arrivano
    come campi hidden nel POST.
    """
    class Meta:
        model = DifettoCQ
        fields = [
            'zona', 'tipo_difetto', 'descrizione_altro',
            'gravita', 'postazione_responsabile',
            'azione_correttiva', 'foto', 'note',
        ]
        widgets = {
            'zona': forms.HiddenInput(),
            'tipo_difetto': forms.HiddenInput(),
            'descrizione_altro': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Descrivi il difetto...',
            }),
            'gravita': forms.HiddenInput(),
            'postazione_responsabile': forms.HiddenInput(),
            'azione_correttiva': forms.HiddenInput(),
            'foto': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['descrizione_altro'].required = False
        self.fields['foto'].required = False
        self.fields['note'].required = False


class BaseDifettiFormSet(BaseModelFormSet):
    def clean(self):
        """Almeno un difetto deve essere compilato se esito = non_ok."""
        if any(self.errors):
            return
        forms_with_data = [
            f for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get('DELETE', False)
        ]
        if not forms_with_data:
            raise forms.ValidationError(
                "Inserisci almeno un difetto quando l'esito è Non OK."
            )


DifettiFormSet = modelformset_factory(
    DifettoCQ,
    form=DifettoCQForm,
    formset=BaseDifettiFormSet,
    extra=1,
    can_delete=True,
)


class ModificaPunteggioForm(forms.ModelForm):
    class Meta:
        model = ModificaPunteggio
        fields = ['operatore', 'punti', 'motivazione']
        widgets = {
            'operatore': forms.Select(attrs={'class': 'form-select'}),
            'punti': forms.NumberInput(attrs={'class': 'form-control'}),
            'motivazione': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operatore'].queryset = get_operatori_queryset()
        self.fields['motivazione'].label = 'Motivazione (obbligatoria)'


class ImpostazionePremioForm(forms.ModelForm):
    class Meta:
        model = ImpostazionePremioMensile
        fields = ['anno', 'mese', 'monte_premi', 'note']
        widgets = {
            'anno': forms.NumberInput(attrs={'class': 'form-control', 'min': 2024, 'max': 2100}),
            'mese': forms.Select(
                attrs={'class': 'form-select'},
                choices=[
                    (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
                    (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
                    (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
                ],
            ),
            'monte_premi': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['note'].required = False
