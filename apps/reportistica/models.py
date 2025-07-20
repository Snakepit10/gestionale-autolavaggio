from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from apps.core.models import Categoria
from apps.clienti.models import Cliente
from apps.ordini.models import Ordine


class ReportPersonalizzato(models.Model):
    """Report personalizzati creati dagli utenti"""
    
    TIPO_CHOICES = [
        ('vendite', 'Report Vendite'),
        ('clienti', 'Report Clienti'),
        ('servizi', 'Report Servizi'),
        ('postazioni', 'Report Postazioni'),
        ('abbonamenti', 'Report Abbonamenti'),
        ('inventario', 'Report Inventario'),
    ]
    
    FORMATO_CHOICES = [
        ('pdf', 'PDF'),
        ('excel', 'Excel'),
        ('csv', 'CSV'),
    ]
    
    PERIODO_CHOICES = [
        ('oggi', 'Oggi'),
        ('settimana', 'Settimana corrente'),
        ('mese', 'Mese corrente'),
        ('trimestre', 'Trimestre corrente'),
        ('anno', 'Anno corrente'),
        ('personalizzato', 'Periodo personalizzato'),
    ]
    
    nome = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    creato_da = models.ForeignKey(User, on_delete=models.CASCADE)
    data_creazione = models.DateTimeField(auto_now_add=True)
    
    # Configurazione report
    periodo_default = models.CharField(max_length=20, choices=PERIODO_CHOICES, default='mese')
    formato_default = models.CharField(max_length=10, choices=FORMATO_CHOICES, default='pdf')
    
    # Filtri personalizzati (JSON)
    filtri_custom = models.JSONField(default=dict, blank=True)
    
    # Configurazione colonne (JSON)
    colonne_visibili = models.JSONField(default=list, blank=True)
    
    # Configurazione grafici (JSON)
    grafici_inclusi = models.JSONField(default=list, blank=True)
    
    # Impostazioni invio automatico
    invio_automatico = models.BooleanField(default=False)
    frequenza_invio = models.CharField(
        max_length=20,
        choices=[
            ('giornaliero', 'Giornaliero'),
            ('settimanale', 'Settimanale'),
            ('mensile', 'Mensile'),
        ],
        blank=True
    )
    email_destinatari = models.JSONField(default=list, blank=True)
    prossimo_invio = models.DateTimeField(null=True, blank=True)
    
    attivo = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Report Personalizzato"
        verbose_name_plural = "Report Personalizzati"
        ordering = ['-data_creazione']
    
    def __str__(self):
        return self.nome


class EsecuzioneReport(models.Model):
    """Storico delle esecuzioni dei report"""
    
    STATO_CHOICES = [
        ('in_corso', 'In corso'),
        ('completato', 'Completato'),
        ('errore', 'Errore'),
    ]
    
    report = models.ForeignKey(ReportPersonalizzato, on_delete=models.CASCADE, related_name='esecuzioni')
    eseguito_da = models.ForeignKey(User, on_delete=models.CASCADE)
    data_esecuzione = models.DateTimeField(auto_now_add=True)
    data_completamento = models.DateTimeField(null=True, blank=True)
    
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='in_corso')
    messaggio_errore = models.TextField(blank=True)
    
    # Parametri utilizzati per l'esecuzione
    parametri_esecuzione = models.JSONField(default=dict)
    
    # File generato
    file_output = models.FileField(upload_to='reports/', blank=True)
    dimensione_file = models.BigIntegerField(null=True, blank=True)
    
    # Statistiche
    tempo_esecuzione_secondi = models.FloatField(null=True, blank=True)
    righe_elaborate = models.IntegerField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Esecuzione Report"
        verbose_name_plural = "Esecuzioni Report"
        ordering = ['-data_esecuzione']
    
    def __str__(self):
        return f"{self.report.nome} - {self.data_esecuzione.strftime('%d/%m/%Y %H:%M')}"


class Dashboard(models.Model):
    """Dashboard personalizzate per diversi ruoli"""
    
    TIPO_DASHBOARD_CHOICES = [
        ('admin', 'Amministratore'),
        ('operatore', 'Operatore'),
        ('manager', 'Manager'),
        ('cliente', 'Cliente'),
    ]
    
    nome = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True)
    tipo_dashboard = models.CharField(max_length=20, choices=TIPO_DASHBOARD_CHOICES)
    
    # Configurazione layout (JSON)
    layout_configurazione = models.JSONField(default=dict)
    
    # Widget inclusi (JSON)
    widget_configurazione = models.JSONField(default=list)
    
    # Permessi
    utenti_autorizzati = models.ManyToManyField(User, blank=True)
    pubblico = models.BooleanField(default=False)
    
    creato_da = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dashboard_create')
    data_creazione = models.DateTimeField(auto_now_add=True)
    data_modifica = models.DateTimeField(auto_now=True)
    
    attivo = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Dashboard"
        verbose_name_plural = "Dashboard"
        ordering = ['tipo_dashboard', 'nome']
    
    def __str__(self):
        return f"{self.nome} ({self.get_tipo_dashboard_display()})"


class KPI(models.Model):
    """Indicatori di performance chiave"""
    
    TIPO_KPI_CHOICES = [
        ('fatturato', 'Fatturato'),
        ('ordini', 'Numero Ordini'),
        ('clienti', 'Clienti'),
        ('servizi', 'Servizi'),
        ('efficienza', 'Efficienza'),
        ('soddisfazione', 'Soddisfazione'),
    ]
    
    PERIODO_CALCOLO_CHOICES = [
        ('tempo_reale', 'Tempo reale'),
        ('giornaliero', 'Giornaliero'),
        ('settimanale', 'Settimanale'),
        ('mensile', 'Mensile'),
    ]
    
    nome = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_KPI_CHOICES)
    
    # Formula di calcolo (può essere una query SQL semplificata)
    formula_calcolo = models.TextField(
        help_text="Formula o query per calcolare il KPI"
    )
    
    # Configurazione visualizzazione
    unita_misura = models.CharField(max_length=20, default='')
    formato_numero = models.CharField(
        max_length=20,
        choices=[
            ('numero', 'Numero'),
            ('percentuale', 'Percentuale'),
            ('valuta', 'Valuta'),
            ('tempo', 'Tempo'),
        ],
        default='numero'
    )
    
    # Soglie di allarme
    soglia_minima = models.FloatField(null=True, blank=True)
    soglia_massima = models.FloatField(null=True, blank=True)
    
    # Frequenza aggiornamento
    periodo_calcolo = models.CharField(max_length=20, choices=PERIODO_CALCOLO_CHOICES, default='giornaliero')
    ultimo_aggiornamento = models.DateTimeField(null=True, blank=True)
    prossimo_aggiornamento = models.DateTimeField(null=True, blank=True)
    
    # Valore corrente
    valore_corrente = models.FloatField(null=True, blank=True)
    valore_precedente = models.FloatField(null=True, blank=True)
    tendenza = models.CharField(
        max_length=10,
        choices=[
            ('crescita', 'Crescita'),
            ('stabile', 'Stabile'),
            ('decrescita', 'Decrescita'),
        ],
        blank=True
    )
    
    attivo = models.BooleanField(default=True)
    visibile_dashboard = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "KPI"
        verbose_name_plural = "KPI"
        ordering = ['tipo', 'nome']
    
    def __str__(self):
        return self.nome


class StoricoCambiamenti(models.Model):
    """Traccia i cambiamenti importanti nel sistema per audit"""
    
    TIPO_OPERAZIONE_CHOICES = [
        ('create', 'Creazione'),
        ('update', 'Modifica'),
        ('delete', 'Eliminazione'),
        ('login', 'Accesso'),
        ('logout', 'Disconnessione'),
        ('export', 'Esportazione'),
        ('import', 'Importazione'),
    ]
    
    utente = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    data_operazione = models.DateTimeField(auto_now_add=True)
    
    tipo_operazione = models.CharField(max_length=20, choices=TIPO_OPERAZIONE_CHOICES)
    modello_interessato = models.CharField(max_length=100)
    oggetto_id = models.CharField(max_length=100, blank=True)
    
    descrizione = models.TextField()
    dettagli_json = models.JSONField(default=dict, blank=True)
    
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Storico Cambiamento"
        verbose_name_plural = "Storico Cambiamenti"
        ordering = ['-data_operazione']
        indexes = [
            models.Index(fields=['utente', 'data_operazione']),
            models.Index(fields=['modello_interessato', 'data_operazione']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_operazione_display()} - {self.modello_interessato} - {self.data_operazione.strftime('%d/%m/%Y %H:%M')}"