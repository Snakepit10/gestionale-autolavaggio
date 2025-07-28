from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Cliente(models.Model):
    TIPO_CHOICES = [
        ('privato', 'Privato'),
        ('azienda', 'Azienda'),
    ]
    
    # Dati comuni
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    email = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=20)
    indirizzo = models.TextField(blank=True)
    cap = models.CharField(max_length=10, blank=True)
    citta = models.CharField(max_length=100, blank=True)
    
    # Dati per privati
    nome = models.CharField(max_length=100, blank=True)
    cognome = models.CharField(max_length=100, blank=True)
    codice_fiscale = models.CharField(max_length=16, blank=True)
    
    # Dati per aziende
    ragione_sociale = models.CharField(max_length=200, blank=True)
    partita_iva = models.CharField(max_length=11, blank=True)
    codice_sdi = models.CharField(max_length=7, blank=True)
    pec = models.EmailField(blank=True)
    
    # Account online
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
    consenso_marketing = models.BooleanField(default=False)
    data_registrazione = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        # Applica title case ai campi di testo
        if self.nome:
            self.nome = self.nome.title()
        if self.cognome:
            self.cognome = self.cognome.title()
        if self.ragione_sociale:
            self.ragione_sociale = self.ragione_sociale.title()
        if self.citta:
            self.citta = self.citta.title()
        if self.indirizzo:
            self.indirizzo = self.indirizzo.title()
        super().save(*args, **kwargs)
    
    class Meta:
        verbose_name_plural = "Clienti"
    
    def __str__(self):
        if self.tipo == 'privato':
            return f"{self.cognome} {self.nome}".strip()
        return self.ragione_sociale
    
    @property
    def nome_completo(self):
        if self.tipo == 'privato':
            return f"{self.cognome} {self.nome}".strip()
        return self.ragione_sociale
    
    def get_ordini_totali(self):
        return self.ordine_set.count()
    
    def get_spesa_totale(self):
        from django.db.models import Sum
        totale = self.ordine_set.aggregate(Sum('totale_finale'))['totale_finale__sum']
        return totale or 0


class PuntiFedelta(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='punti_fedelta')
    punti_totali = models.IntegerField(default=0)
    punti_utilizzati = models.IntegerField(default=0)
    
    class Meta:
        verbose_name_plural = "Punti Fedeltà"
    
    def __str__(self):
        return f"{self.cliente} - {self.punti_disponibili} punti"
    
    @property
    def punti_disponibili(self):
        return self.punti_totali - self.punti_utilizzati


class MovimentoPunti(models.Model):
    TIPO_CHOICES = [
        ('accumulo', 'Accumulo'),
        ('utilizzo', 'Utilizzo'),
        ('scadenza', 'Scadenza'),
        ('bonus', 'Bonus'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='movimenti_punti')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    punti = models.IntegerField(help_text="Positivi per accumulo, negativi per utilizzo")
    descrizione = models.CharField(max_length=200)
    ordine = models.ForeignKey(
        'ordini.Ordine', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    data_movimento = models.DateTimeField(auto_now_add=True)
    data_scadenza = models.DateField(null=True, blank=True)
    
    class Meta:
        ordering = ['-data_movimento']
        verbose_name_plural = "Movimenti Punti"
    
    def __str__(self):
        return f"{self.cliente} - {self.get_tipo_display()} ({self.punti} punti)"