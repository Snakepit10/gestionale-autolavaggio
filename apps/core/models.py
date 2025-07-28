from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal


class Categoria(models.Model):
    nome = models.CharField(max_length=100)
    descrizione = models.TextField(blank=True)
    ordine_visualizzazione = models.IntegerField(default=0)
    attiva = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['ordine_visualizzazione', 'nome']
        verbose_name_plural = "Categorie"
    
    def __str__(self):
        return self.nome


class Postazione(models.Model):
    nome = models.CharField(max_length=100)
    descrizione = models.TextField(blank=True)
    attiva = models.BooleanField(default=True)
    ordine_visualizzazione = models.IntegerField(default=0)
    
    # Configurazione stampante
    stampante_comande = models.ForeignKey(
        'StampanteRete', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL,
        related_name='postazioni_comande'
    )
    
    class Meta:
        ordering = ['ordine_visualizzazione', 'nome']
        verbose_name_plural = "Postazioni"
    
    def __str__(self):
        return self.nome
    
    def get_ordini_in_coda(self):
        from apps.ordini.models import ItemOrdine
        
        return ItemOrdine.objects.filter(
            postazione_assegnata=self,
            stato__in=['in_attesa', 'in_lavorazione'],
            ordine__stato__in=['in_attesa', 'in_lavorazione']  # Escludi anche ordini completati/annullati
        ).select_related('ordine', 'servizio_prodotto').order_by('ordine__numero_progressivo')
    
    def get_tempo_medio_servizio(self, servizio):
        from apps.ordini.models import ItemOrdine
        from django.db.models import Avg
        
        try:
            tempo_medio = ItemOrdine.objects.filter(
                postazione_assegnata=self,
                servizio_prodotto=servizio,
                stato='completato',
                fine_lavorazione__isnull=False,
                inizio_lavorazione__isnull=False
            ).extra(
                select={'durata': 'EXTRACT(EPOCH FROM (fine_lavorazione - inizio_lavorazione))/60'}
            ).aggregate(media=Avg('durata'))['media']
            
            return tempo_medio or servizio.durata_minuti
        except Exception:
            return servizio.durata_minuti


class ServizioProdotto(models.Model):
    TIPO_CHOICES = [
        ('servizio', 'Servizio'),
        ('prodotto', 'Prodotto'),
    ]
    
    titolo = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='servizio')
    prezzo = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)
    descrizione = models.TextField()
    
    # Campi per servizi
    durata_minuti = models.IntegerField(default=30, help_text="Durata stimata in minuti (solo per servizi)")
    postazioni = models.ManyToManyField(Postazione, blank=True, help_text="Postazioni che possono erogare questo servizio")
    
    # Campi per prodotti
    quantita_disponibile = models.IntegerField(default=-1, help_text="-1 = illimitata (per servizi)")
    quantita_minima_alert = models.IntegerField(default=5, help_text="Soglia per alert scorte basse")
    codice_prodotto = models.CharField(max_length=50, blank=True, help_text="SKU/codice interno")
    
    attivo = models.BooleanField(default=True)
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['categoria__ordine_visualizzazione', 'titolo']
        verbose_name_plural = "Servizi e Prodotti"
    
    def __str__(self):
        return f"{self.titolo} ({self.get_tipo_display()})"
    
    @property
    def scorta_bassa(self):
        if self.tipo == 'prodotto' and self.quantita_disponibile > 0:
            return self.quantita_disponibile <= self.quantita_minima_alert
        return False
    
    @property
    def disponibile(self):
        if self.tipo == 'servizio':
            return self.attivo
        return self.attivo and (self.quantita_disponibile == -1 or self.quantita_disponibile > 0)


class Sconto(models.Model):
    TIPO_CHOICES = [
        ('percentuale', 'Percentuale'),
        ('importo', 'Importo Fisso'),
    ]
    
    titolo = models.CharField(max_length=100)
    tipo_sconto = models.CharField(max_length=20, choices=TIPO_CHOICES)
    valore = models.DecimalField(max_digits=10, decimal_places=2)
    attivo = models.BooleanField(default=True)
    
    class Meta:
        verbose_name_plural = "Sconti"
    
    def __str__(self):
        if self.tipo_sconto == 'percentuale':
            return f"{self.titolo} ({self.valore}%)"
        return f"{self.titolo} (€{self.valore})"
    
    def calcola_sconto(self, importo):
        if self.tipo_sconto == 'percentuale':
            return importo * (self.valore / 100)
        return min(self.valore, importo)


class StampanteRete(models.Model):
    TIPO_CHOICES = [
        ('scontrino', 'Stampante Scontrini'),
        ('comanda', 'Stampante Comande'),
        ('report', 'Stampante Report'),
    ]
    
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    indirizzo_ip = models.GenericIPAddressField()
    porta = models.IntegerField(default=9100)
    modello = models.CharField(max_length=100)
    larghezza_carta = models.IntegerField(default=80, help_text="Larghezza carta in mm")
    attiva = models.BooleanField(default=True)
    predefinita = models.BooleanField(default=False)
    
    class Meta:
        unique_together = [['tipo', 'predefinita']]
        verbose_name_plural = "Stampanti di Rete"
    
    def __str__(self):
        return f"{self.nome} ({self.get_tipo_display()})"
    
    def save(self, *args, **kwargs):
        if self.predefinita:
            # Assicura che solo una stampante per tipo sia predefinita
            StampanteRete.objects.filter(
                tipo=self.tipo, 
                predefinita=True
            ).exclude(pk=self.pk).update(predefinita=False)
        super().save(*args, **kwargs)


class MovimentoScorte(models.Model):
    TIPO_MOVIMENTO = [
        ('carico', 'Carico'),
        ('scarico', 'Scarico'),
        ('rettifica', 'Rettifica Inventario'),
    ]
    
    prodotto = models.ForeignKey(ServizioProdotto, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_MOVIMENTO)
    quantita = models.IntegerField(help_text="Positivo per carico, negativo per scarico")
    quantita_prima = models.IntegerField()
    quantita_dopo = models.IntegerField()
    riferimento_ordine = models.ForeignKey(
        'ordini.Ordine', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    nota = models.TextField(blank=True)
    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    data_movimento = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-data_movimento']
        verbose_name_plural = "Movimenti Scorte"
    
    def __str__(self):
        return f"{self.prodotto.titolo} - {self.get_tipo_display()} ({self.quantita})"