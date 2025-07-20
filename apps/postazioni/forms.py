from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML
from .models import Postazione
from apps.core.models import ServizioProdotto


class PostazioneForm(forms.ModelForm):
    servizi = forms.ModelMultipleChoiceField(
        queryset=ServizioProdotto.objects.filter(tipo='servizio', attivo=True),
        widget=forms.CheckboxSelectMultiple(),
        required=False,
        help_text="Seleziona i servizi che questa postazione può erogare"
    )
    
    class Meta:
        model = Postazione
        fields = ['nome', 'descrizione', 'ordine_visualizzazione', 'stampante_comande', 'attiva']
        widgets = {
            'descrizione': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Div(
                Field('nome'),
                Field('descrizione'),
                css_class='col-md-6'
            ),
            Div(
                Field('ordine_visualizzazione'),
                Field('stampante_comande'),
                Field('attiva'),
                css_class='col-md-6'
            ),
            Div(
                HTML('<h5>Servizi Supportati</h5>'),
                Field('servizi'),
                css_class='col-12'
            )
        )
        
        # Precompila i servizi se stiamo modificando una postazione esistente
        if self.instance.pk:
            # self.initial['servizi'] = self.instance.servizioProdotto_set.filter(tipo='servizio')
            # Temporaneamente disabilitato - utilizzare la relazione inversa dai servizi
            self.initial['servizi'] = ServizioProdotto.objects.filter(
                postazioni=self.instance, 
                tipo='servizio'
            )
    
    def save(self, commit=True):
        postazione = super().save(commit=commit)
        
        if commit:
            # Aggiorna la relazione many-to-many con i servizi
            servizi_selezionati = self.cleaned_data.get('servizi', [])
            
            # Rimuovi la postazione da tutti i servizi
            ServizioProdotto.objects.filter(
                postazioni=postazione
            ).update()
            
            # Aggiungi la postazione ai servizi selezionati
            for servizio in servizi_selezionati:
                servizio.postazioni.add(postazione)
        
        return postazione