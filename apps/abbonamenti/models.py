from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, timedelta, date
import uuid
import secrets
import string


class ConfigurazioneAbbonamento(models.Model):
    MODALITA_TARGA_CHOICES = [
        ('singola', 'Targa Singola'),
        ('multipla', 'Targhe Multiple'),
        ('libera', 'Senza Vincoli Targa'),
    ]
    
    PERIODICITA_CHOICES = [
        ('giornaliero', 'Giornaliero'),
        ('settimanale', 'Settimanale'),
        ('mensile', 'Mensile'),
        ('trimestrale', 'Trimestrale'),
        ('semestrale', 'Semestrale'),
        ('annuale', 'Annuale'),
    ]
    
    DURATA_CHOICES = [
        ('1_mese', '1 Mese'),
        ('3_mesi', '3 Mesi'),
        ('6_mesi', '6 Mesi'),
        ('12_mesi', '12 Mesi'),
    ]
    
    # Info base
    titolo = models.CharField(max_length=200)
    descrizione = models.TextField()
    prezzo = models.DecimalField(max_digits=10, decimal_places=2)
    attiva = models.BooleanField(default=True)
    
    # Modalità targa
    modalita_targa = models.CharField(max_length=20, choices=MODALITA_TARGA_CHOICES)
    numero_massimo_targhe = models.IntegerField(default=1)
    
    # Frequenza accessi
    periodicita_reset = models.CharField(max_length=20, choices=PERIODICITA_CHOICES)
    
    # Durata abbonamento
    durata = models.CharField(max_length=20, choices=DURATA_CHOICES)
    giorni_durata = models.IntegerField(help_text="Durata in giorni")
    
    # Rinnovo
    rinnovo_automatico = models.BooleanField(default=False)
    giorni_preavviso_scadenza = models.IntegerField(default=7)
    
    # Condizioni
    termini_condizioni = models.TextField(blank=True)
    
    creata_il = models.DateTimeField(auto_now_add=True)
    aggiornata_il = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Configurazioni Abbonamento"
    
    def __str__(self):
        return self.titolo
    
    def genera_termini_condizioni(self):
        # Genera automaticamente i termini e condizioni
        termini = f"""
ABBONAMENTO {self.titolo.upper()}

Prezzo: €{self.prezzo}
Durata: {self.get_durata_display()}
Modalità targa: {self.get_modalita_targa_display()}

SERVIZI INCLUSI:
"""
        for servizio_incluso in self.servizi_inclusi.all():
            termini += f"- {servizio_incluso.servizio.titolo}: {servizio_incluso.quantita_inclusa} accessi per {self.get_periodicita_reset_display().lower()}\n"
        
        termini += f"""
CONDIZIONI:
- L'abbonamento ha validità di {self.giorni_durata} giorni dalla data di attivazione
- I contatori degli accessi si azzerano ogni {self.get_periodicita_reset_display().lower()}
"""
        
        if self.modalita_targa != 'libera':
            termini += f"- Abbonamento vincolato a {self.numero_massimo_targhe} targa/e\n"
        
        if self.rinnovo_automatico:
            termini += f"- Rinnovo automatico attivo (preavviso {self.giorni_preavviso_scadenza} giorni)\n"
        
        self.termini_condizioni = termini
        self.save(update_fields=['termini_condizioni'])


class ServizioInclusoAbbonamento(models.Model):
    configurazione = models.ForeignKey(
        ConfigurazioneAbbonamento, 
        on_delete=models.CASCADE,
        related_name='servizi_inclusi'
    )
    servizio = models.ForeignKey('core.ServizioProdotto', on_delete=models.CASCADE)
    quantita_inclusa = models.IntegerField(help_text="Numero accessi per periodo")
    
    class Meta:
        unique_together = ['configurazione', 'servizio']
        verbose_name_plural = "Servizi Inclusi Abbonamento"
    
    def __str__(self):
        return f"{self.servizio.titolo} x{self.quantita_inclusa}"


class ConfigurazioneAccessiGiorno(models.Model):
    GIORNI_SETTIMANA = [
        (0, 'Lunedì'),
        (1, 'Martedì'),
        (2, 'Mercoledì'),
        (3, 'Giovedì'),
        (4, 'Venerdì'),
        (5, 'Sabato'),
        (6, 'Domenica'),
    ]
    
    configurazione = models.ForeignKey(
        ConfigurazioneAbbonamento,
        on_delete=models.CASCADE,
        related_name='accessi_giorni'
    )
    giorno_settimana = models.IntegerField(choices=GIORNI_SETTIMANA)
    numero_accessi = models.IntegerField(help_text="Numero massimo accessi per questo giorno")
    
    class Meta:
        unique_together = ['configurazione', 'giorno_settimana']
        verbose_name_plural = "Configurazioni Accessi per Giorno"


class Abbonamento(models.Model):
    STATO_CHOICES = [
        ('attivo', 'Attivo'),
        ('sospeso', 'Sospeso'),
        ('scaduto', 'Scaduto'),
        ('annullato', 'Annullato'),
    ]
    
    cliente = models.ForeignKey('clienti.Cliente', on_delete=models.CASCADE, related_name='abbonamenti')
    configurazione = models.ForeignKey(ConfigurazioneAbbonamento, on_delete=models.PROTECT)
    
    # Codici di accesso
    codice_accesso = models.CharField(max_length=20, unique=True, blank=True)
    codice_nfc = models.CharField(max_length=32, unique=True, blank=True)
    
    # Date
    data_attivazione = models.DateField(auto_now_add=True)
    data_scadenza = models.DateField()
    data_ultimo_accesso = models.DateTimeField(null=True, blank=True)
    
    # Stato
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='attivo')
    
    # Pagamento
    ordine_acquisto = models.OneToOneField(
        'ordini.Ordine',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    
    # Tessera fisica
    tessera_stampata = models.BooleanField(default=False)
    numero_tessera = models.CharField(max_length=20, blank=True)
    
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-data_attivazione']
        verbose_name_plural = "Abbonamenti"
    
    def __str__(self):
        return f"{self.cliente} - {self.configurazione.titolo}"
    
    def save(self, *args, **kwargs):
        if not self.codice_accesso:
            self.codice_accesso = self.genera_codice_accesso()
        if not self.codice_nfc:
            self.codice_nfc = self.genera_codice_nfc()
        if not self.data_scadenza:
            self.data_scadenza = self.calcola_data_scadenza()
        super().save(*args, **kwargs)
    
    def genera_codice_accesso(self):
        # Genera un codice di accesso di 8 caratteri
        while True:
            codice = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if not Abbonamento.objects.filter(codice_accesso=codice).exists():
                return codice
    
    def genera_codice_nfc(self):
        # Genera un UUID per il codice NFC
        return str(uuid.uuid4()).replace('-', '')
    
    def calcola_data_scadenza(self):
        giorni = self.configurazione.giorni_durata
        return date.today() + timedelta(days=giorni)
    
    @property
    def is_attivo(self):
        return (
            self.stato == 'attivo' and 
            self.data_scadenza >= date.today()
        )
    
    @property
    def giorni_rimanenti(self):
        if self.data_scadenza:
            delta = self.data_scadenza - date.today()
            return max(0, delta.days)
        return 0
    
    def get_contatore_corrente(self, servizio):
        """Ottiene il contatore attuale per un servizio"""
        contatore, created = ContatoreAccessiAbbonamento.objects.get_or_create(
            abbonamento=self,
            servizio=servizio,
            periodo_inizio=self.get_inizio_periodo_corrente(),
            defaults={'accessi_effettuati': 0}
        )
        return contatore
    
    def get_inizio_periodo_corrente(self):
        """Calcola l'inizio del periodo corrente basato sulla periodicità"""
        oggi = date.today()
        periodicita = self.configurazione.periodicita_reset
        
        if periodicita == 'giornaliero':
            return oggi
        elif periodicita == 'settimanale':
            # Lunedì della settimana corrente
            return oggi - timedelta(days=oggi.weekday())
        elif periodicita == 'mensile':
            return oggi.replace(day=1)
        elif periodicita == 'trimestrale':
            mese_trimestre = ((oggi.month - 1) // 3) * 3 + 1
            return oggi.replace(month=mese_trimestre, day=1)
        elif periodicita == 'semestrale':
            mese_semestre = 1 if oggi.month <= 6 else 7
            return oggi.replace(month=mese_semestre, day=1)
        elif periodicita == 'annuale':
            return oggi.replace(month=1, day=1)
        
        return oggi
    
    def verifica_accesso_disponibile(self, servizio, targa=None):
        """
        Verifica se l'accesso è disponibile per il servizio richiesto
        Returns: (bool, str) - (autorizzato, motivo_rifiuto)
        """
        # 1. Verifica stato abbonamento
        if self.stato != 'attivo':
            return False, f"Abbonamento {self.stato}"
        
        # 2. Verifica scadenza
        if date.today() > self.data_scadenza:
            return False, "Abbonamento scaduto"
        
        # 3. Verifica servizio incluso
        servizio_incluso = self.configurazione.servizi_inclusi.filter(
            servizio=servizio
        ).first()
        if not servizio_incluso:
            return False, "Servizio non incluso nell'abbonamento"
        
        # 4. Verifica targa (se richiesta)
        if self.configurazione.modalita_targa != 'libera':
            if not targa:
                return False, "Targa richiesta"
            if not self.targhe.filter(targa=targa, attiva=True).exists():
                return False, "Targa non autorizzata"
        
        # 5. Verifica limiti accessi
        contatore = self.get_contatore_corrente(servizio)
        if contatore.accessi_effettuati >= servizio_incluso.quantita_inclusa:
            return False, "Limite accessi raggiunto per il periodo"
        
        # 6. Verifica accessi giornalieri (se configurati)
        oggi = date.today()
        giorno_settimana = oggi.weekday()
        
        config_giorno = self.configurazione.accessi_giorni.filter(
            giorno_settimana=giorno_settimana
        ).first()
        
        if config_giorno:
            accessi_oggi = AccessoAbbonamento.objects.filter(
                abbonamento=self,
                servizio=servizio,
                data_ora__date=oggi,
                autorizzato=True
            ).count()
            
            if accessi_oggi >= config_giorno.numero_accessi:
                return False, "Limite giornaliero raggiunto"
        
        return True, ""


class TargaAbbonamento(models.Model):
    abbonamento = models.ForeignKey(Abbonamento, on_delete=models.CASCADE, related_name='targhe')
    targa = models.CharField(max_length=10)
    attiva = models.BooleanField(default=True)
    data_aggiunta = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['abbonamento', 'targa']
        verbose_name_plural = "Targhe Abbonamento"
    
    def __str__(self):
        return f"{self.abbonamento} - {self.targa}"


class ContatoreAccessiAbbonamento(models.Model):
    abbonamento = models.ForeignKey(Abbonamento, on_delete=models.CASCADE, related_name='contatori')
    servizio = models.ForeignKey('core.ServizioProdotto', on_delete=models.CASCADE)
    periodo_inizio = models.DateField()
    accessi_effettuati = models.IntegerField(default=0)
    
    class Meta:
        unique_together = ['abbonamento', 'servizio', 'periodo_inizio']
        verbose_name_plural = "Contatori Accessi Abbonamento"
    
    def __str__(self):
        return f"{self.abbonamento} - {self.servizio.titolo} ({self.accessi_effettuati})"


class AccessoAbbonamento(models.Model):
    METODO_VERIFICA_CHOICES = [
        ('nfc', 'NFC'),
        ('qr', 'QR Code'),
        ('codice', 'Codice Manuale'),
        ('tessera', 'Tessera Fisica'),
    ]
    
    abbonamento = models.ForeignKey(Abbonamento, on_delete=models.CASCADE, related_name='accessi')
    servizio = models.ForeignKey('core.ServizioProdotto', on_delete=models.CASCADE)
    data_ora = models.DateTimeField(auto_now_add=True)
    
    # Dettagli accesso
    targa_utilizzata = models.CharField(max_length=10, blank=True)
    postazione = models.ForeignKey(
        'core.Postazione',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    metodo_verifica = models.CharField(max_length=20, choices=METODO_VERIFICA_CHOICES)
    
    # Autorizzazione
    autorizzato = models.BooleanField(default=False)
    motivo_rifiuto = models.CharField(max_length=200, blank=True)
    
    # Operatore
    operatore = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    
    class Meta:
        ordering = ['-data_ora']
        verbose_name_plural = "Accessi Abbonamento"
    
    def __str__(self):
        status = "Autorizzato" if self.autorizzato else f"Rifiutato ({self.motivo_rifiuto})"
        return f"{self.abbonamento} - {self.servizio.titolo} - {status}"