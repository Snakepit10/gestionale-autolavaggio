from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field, HTML, Submit, Row, Column
from crispy_forms.bootstrap import FormActions
from datetime import date, timedelta
from .models import ReportPersonalizzato, Dashboard


class GenerazioneReportForm(forms.Form):
    """Form per generazione report"""
    
    PERIODO_CHOICES = [
        ('oggi', 'Oggi'),
        ('ieri', 'Ieri'),
        ('settimana', 'Settimana corrente'),
        ('settimana_scorsa', 'Settimana scorsa'),
        ('mese', 'Mese corrente'),
        ('mese_scorso', 'Mese scorso'),
        ('trimestre', 'Trimestre corrente'),
        ('anno', 'Anno corrente'),
        ('personalizzato', 'Periodo personalizzato'),
    ]
    
    FORMATO_CHOICES = [
        ('pdf', 'PDF'),
        ('excel', 'Excel (XLSX)'),
        ('csv', 'CSV'),
    ]
    
    periodo_predefinito = forms.ChoiceField(
        choices=PERIODO_CHOICES,
        initial='mese',
        required=False,
        widget=forms.Select(attrs={'onchange': 'toggleDateFields()'})
    )
    
    data_inizio = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False
    )
    
    data_fine = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False
    )
    
    formato = forms.ChoiceField(
        choices=FORMATO_CHOICES,
        initial='pdf'
    )
    
    includi_grafici = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Includi grafici nel report (solo PDF/Excel)"
    )
    
    raggruppa_per = forms.ChoiceField(
        choices=[
            ('giorno', 'Per giorno'),
            ('settimana', 'Per settimana'),
            ('mese', 'Per mese'),
            ('categoria', 'Per categoria'),
            ('postazione', 'Per postazione'),
        ],
        required=False
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Row(
                Column('periodo_predefinito', css_class='form-group col-md-6 mb-0'),
                Column('formato', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            HTML('<div id="date-fields" style="display: none;">'),
            Row(
                Column('data_inizio', css_class='form-group col-md-6 mb-0'),
                Column('data_fine', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            HTML('</div>'),
            Row(
                Column('includi_grafici', css_class='form-group col-md-6 mb-0'),
                Column('raggruppa_per', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            FormActions(
                Submit('submit', 'Genera Report', css_class='btn-primary btn-lg'),
                css_class='mt-3'
            )
        )
        
        # Imposta date di default
        oggi = date.today()
        self.fields['data_inizio'].initial = oggi.replace(day=1)  # Primo del mese
        self.fields['data_fine'].initial = oggi
    
    def clean(self):
        cleaned_data = super().clean()
        periodo = cleaned_data.get('periodo_predefinito')
        data_inizio = cleaned_data.get('data_inizio')
        data_fine = cleaned_data.get('data_fine')
        
        if periodo == 'personalizzato':
            if not data_inizio or not data_fine:
                raise forms.ValidationError(
                    'Per il periodo personalizzato devi specificare data inizio e fine'
                )
            if data_inizio > data_fine:
                raise forms.ValidationError(
                    'La data di inizio deve essere precedente alla data di fine'
                )
        
        return cleaned_data


class FiltriReportForm(forms.Form):
    """Form per filtri avanzati dei report"""
    
    clienti = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Filtra per clienti specifici"
    )
    
    categorie = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Filtra per categorie"
    )
    
    postazioni = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Filtra per postazioni"
    )
    
    importo_minimo = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        help_text="Importo minimo ordine"
    )
    
    importo_massimo = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        help_text="Importo massimo ordine"
    )
    
    solo_clienti_registrati = forms.BooleanField(
        required=False,
        help_text="Includi solo ordini di clienti registrati"
    )
    
    solo_abbonati = forms.BooleanField(
        required=False,
        help_text="Includi solo clienti con abbonamento attivo"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Popola le queryset
        from apps.clienti.models import Cliente
        from apps.core.models import Categoria, Postazione
        
        self.fields['clienti'].queryset = Cliente.objects.filter(attivo=True).order_by('cognome', 'nome')
        self.fields['categorie'].queryset = Categoria.objects.filter(attiva=True).order_by('nome')
        self.fields['postazioni'].queryset = Postazione.objects.filter(attiva=True).order_by('nome')
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Row(
                Column('importo_minimo', css_class='form-group col-md-6 mb-0'),
                Column('importo_massimo', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('solo_clienti_registrati', css_class='form-group col-md-6 mb-0'),
                Column('solo_abbonati', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Field('categorie'),
            Field('postazioni'),
            Field('clienti'),
        )


class ReportPersonalizzatoForm(forms.ModelForm):
    """Form per creare report personalizzati"""
    
    class Meta:
        model = ReportPersonalizzato
        fields = [
            'nome', 'descrizione', 'tipo', 
            'periodo_default', 'formato_default',
            'invio_automatico', 'frequenza_invio', 'email_destinatari'
        ]
        widgets = {
            'descrizione': forms.Textarea(attrs={'rows': 3}),
            'email_destinatari': forms.Textarea(
                attrs={
                    'rows': 2,
                    'placeholder': 'Un email per riga'
                }
            ),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Row(
                Column('nome', css_class='form-group col-md-8 mb-0'),
                Column('tipo', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Field('descrizione'),
            Row(
                Column('periodo_default', css_class='form-group col-md-6 mb-0'),
                Column('formato_default', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            HTML('<hr>'),
            HTML('<h5>Invio Automatico</h5>'),
            Field('invio_automatico'),
            Row(
                Column('frequenza_invio', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Field('email_destinatari'),
            FormActions(
                Submit('submit', 'Salva Report', css_class='btn-primary'),
                css_class='mt-3'
            )
        )
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.creato_da = self.user
        
        # Processa email destinatari
        if self.cleaned_data.get('email_destinatari'):
            emails = [
                email.strip() 
                for email in self.cleaned_data['email_destinatari'].split('\n')
                if email.strip()
            ]
            instance.email_destinatari = emails
        
        if commit:
            instance.save()
        return instance


class DashboardForm(forms.ModelForm):
    """Form per configurare dashboard"""
    
    widget_disponibili = forms.MultipleChoiceField(
        choices=[
            ('kpi_fatturato', 'KPI Fatturato'),
            ('kpi_ordini', 'KPI Ordini'),
            ('grafico_vendite', 'Grafico Vendite'),
            ('top_servizi', 'Top Servizi'),
            ('ordini_recenti', 'Ordini Recenti'),
            ('statistiche_postazioni', 'Statistiche Postazioni'),
            ('clienti_top', 'Top Clienti'),
            ('scorte_basse', 'Scorte Basse'),
        ],
        widget=forms.CheckboxSelectMultiple(),
        required=False
    )
    
    class Meta:
        model = Dashboard
        fields = ['nome', 'descrizione', 'tipo_dashboard', 'pubblico']
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Row(
                Column('nome', css_class='form-group col-md-8 mb-0'),
                Column('tipo_dashboard', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Field('descrizione'),
            Field('pubblico'),
            HTML('<hr>'),
            HTML('<h5>Widget da Includere</h5>'),
            Field('widget_disponibili'),
            FormActions(
                Submit('submit', 'Salva Dashboard', css_class='btn-primary'),
                css_class='mt-3'
            )
        )
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.creato_da = self.user
        
        # Salva configurazione widget
        widget_selezionati = self.cleaned_data.get('widget_disponibili', [])
        instance.widget_configurazione = [
            {'type': widget, 'enabled': True, 'order': i}
            for i, widget in enumerate(widget_selezionati)
        ]
        
        if commit:
            instance.save()
        return instance


class EsportazioneForm(forms.Form):
    """Form per esportazione dati"""
    
    TIPO_EXPORT_CHOICES = [
        ('ordini', 'Ordini'),
        ('clienti', 'Clienti'),
        ('servizi', 'Servizi e Prodotti'),
        ('abbonamenti', 'Abbonamenti'),
        ('pagamenti', 'Pagamenti'),
        ('completo', 'Backup Completo'),
    ]
    
    tipo_dati = forms.ChoiceField(
        choices=TIPO_EXPORT_CHOICES,
        help_text="Seleziona il tipo di dati da esportare"
    )
    
    formato_export = forms.ChoiceField(
        choices=[
            ('csv', 'CSV'),
            ('excel', 'Excel'),
            ('json', 'JSON'),
        ],
        initial='csv'
    )
    
    data_inizio = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False,
        help_text="Lascia vuoto per tutti i dati"
    )
    
    data_fine = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False
    )
    
    includi_eliminati = forms.BooleanField(
        required=False,
        help_text="Includi record eliminati (soft delete)"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            Row(
                Column('tipo_dati', css_class='form-group col-md-6 mb-0'),
                Column('formato_export', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('data_inizio', css_class='form-group col-md-6 mb-0'),
                Column('data_fine', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Field('includi_eliminati'),
            FormActions(
                Submit('submit', 'Esporta Dati', css_class='btn-success btn-lg'),
                css_class='mt-3'
            )
        )
        
        # Date di default
        oggi = date.today()
        self.fields['data_fine'].initial = oggi
        self.fields['data_inizio'].initial = oggi - timedelta(days=30)


class ConfigurazioneKPIForm(forms.Form):
    """Form per configurazione KPI dashboard"""
    
    kpi_fatturato = forms.BooleanField(required=False, initial=True)
    kpi_ordini = forms.BooleanField(required=False, initial=True)
    kpi_clienti = forms.BooleanField(required=False, initial=True)
    kpi_scontrino_medio = forms.BooleanField(required=False, initial=True)
    
    periodo_confronto = forms.ChoiceField(
        choices=[
            ('giorno_precedente', 'Giorno precedente'),
            ('settimana_precedente', 'Settimana precedente'),
            ('mese_precedente', 'Mese precedente'),
            ('anno_precedente', 'Anno precedente'),
        ],
        initial='mese_precedente'
    )
    
    aggiornamento_automatico = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Aggiorna KPI automaticamente ogni 5 minuti"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.layout = Layout(
            HTML('<h5>KPI da Visualizzare</h5>'),
            Row(
                Column('kpi_fatturato', css_class='form-group col-md-3 mb-0'),
                Column('kpi_ordini', css_class='form-group col-md-3 mb-0'),
                Column('kpi_clienti', css_class='form-group col-md-3 mb-0'),
                Column('kpi_scontrino_medio', css_class='form-group col-md-3 mb-0'),
                css_class='form-row'
            ),
            HTML('<hr>'),
            Row(
                Column('periodo_confronto', css_class='form-group col-md-6 mb-0'),
                Column('aggiornamento_automatico', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            FormActions(
                Submit('submit', 'Salva Configurazione', css_class='btn-primary'),
                css_class='mt-3'
            )
        )