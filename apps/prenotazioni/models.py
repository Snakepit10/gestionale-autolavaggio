from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, timedelta, time
import secrets
import string


class ConfigurazioneSlot(models.Model):
    """Configurazione degli slot disponibili per prenotazioni"""
    GIORNI_SETTIMANA = [
        (0, 'Lunedì'),
        (1, 'Martedì'),
        (2, 'Mercoledì'),
        (3, 'Giovedì'),
        (4, 'Venerdì'),
        (5, 'Sabato'),
        (6, 'Domenica'),
    ]
    
    giorno_settimana = models.IntegerField(choices=GIORNI_SETTIMANA)
    ora_inizio = models.TimeField()
    ora_fine = models.TimeField()
    durata_slot_minuti = models.IntegerField(default=30)
    max_prenotazioni_per_slot = models.IntegerField(default=2)
    servizi_ammessi = models.ManyToManyField('core.ServizioProdotto', blank=True)
    attivo = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['giorno_settimana', 'ora_inizio']
        verbose_name_plural = "Configurazioni Slot"
        ordering = ['giorno_settimana', 'ora_inizio']
    
    def __str__(self):
        return f"{self.get_giorno_settimana_display()} {self.ora_inizio.strftime('%H:%M')}-{self.ora_fine.strftime('%H:%M')}"
    
    def genera_slot_per_data(self, data):
        """Genera uno slot specifico per una data"""
        slot, created = SlotPrenotazione.objects.get_or_create(
            data=data,
            ora_inizio=self.ora_inizio,
            defaults={
                'ora_fine': self.ora_fine,
                'max_prenotazioni': self.max_prenotazioni_per_slot,
                'prenotazioni_attuali': 0,
                'disponibile': True
            }
        )
        return slot


class SlotPrenotazione(models.Model):
    """Slot specifico per una data"""
    data = models.DateField()
    ora_inizio = models.TimeField()
    ora_fine = models.TimeField()
    prenotazioni_attuali = models.IntegerField(default=0)
    max_prenotazioni = models.IntegerField()
    disponibile = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['data', 'ora_inizio']
        ordering = ['data', 'ora_inizio']
        verbose_name_plural = "Slot Prenotazioni"
    
    def __str__(self):
        return f"{self.data} {self.ora_inizio.strftime('%H:%M')}-{self.ora_fine.strftime('%H:%M')}"
    
    @property
    def posti_disponibili(self):
        return max(0, self.max_prenotazioni - self.prenotazioni_attuali)
    
    @property
    def is_disponibile(self):
        # Crea datetime timezone-aware per il confronto
        slot_datetime = timezone.make_aware(datetime.combine(self.data, self.ora_inizio))
        return (
            self.disponibile and 
            self.posti_disponibili > 0 and
            slot_datetime > timezone.now()
        )
    
    def aggiorna_contatori(self):
        """Aggiorna il contatore delle prenotazioni attuali"""
        count = self.prenotazioni.filter(stato='confermata').count()
        if self.prenotazioni_attuali != count:
            self.prenotazioni_attuali = count
            self.save(update_fields=['prenotazioni_attuali'])


class Prenotazione(models.Model):
    STATO_CHOICES = [
        ('confermata', 'Confermata'),
        ('in_attesa', 'In Attesa'),
        ('completata', 'Completata'),
        ('annullata', 'Annullata'),
        ('no_show', 'No Show'),
    ]
    
    cliente = models.ForeignKey(
        'clienti.Cliente', 
        on_delete=models.CASCADE, 
        related_name='prenotazioni'
    )
    slot = models.ForeignKey(
        SlotPrenotazione, 
        on_delete=models.CASCADE, 
        related_name='prenotazioni'
    )
    servizi = models.ManyToManyField('core.ServizioProdotto')
    durata_stimata_minuti = models.IntegerField()
    
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='confermata')
    codice_prenotazione = models.CharField(max_length=10, unique=True, blank=True)
    
    # Riferimento all'ordine quando viene convertita
    ordine = models.OneToOneField(
        'ordini.Ordine', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    
    nota_cliente = models.TextField(blank=True)
    nota_interna = models.TextField(blank=True)
    tipo_auto = models.CharField(max_length=200, blank=True, help_text="Modello e colore dell'auto")
    
    # Notifiche
    promemoria_inviato = models.BooleanField(default=False)
    
    creata_il = models.DateTimeField(auto_now_add=True)
    aggiornata_il = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['slot__data', 'slot__ora_inizio']
        verbose_name_plural = "Prenotazioni"
    
    def __str__(self):
        return f"Prenotazione {self.codice_prenotazione} - {self.cliente}"
    
    def save(self, *args, **kwargs):
        if not self.codice_prenotazione:
            self.codice_prenotazione = self.genera_codice_prenotazione()
        if not self.durata_stimata_minuti:
            self.durata_stimata_minuti = self.calcola_durata_stimata()
        super().save(*args, **kwargs)
        
        # Aggiorna i contatori dello slot
        self.slot.aggiorna_contatori()
    
    def genera_codice_prenotazione(self):
        """Genera un codice univoco di 8 caratteri"""
        while True:
            codice = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if not Prenotazione.objects.filter(codice_prenotazione=codice).exists():
                return codice
    
    def calcola_durata_stimata(self):
        """Calcola la durata stimata in base ai servizi selezionati"""
        if self.pk:  # Solo se l'oggetto è già salvato e ha servizi
            return sum(servizio.durata_minuti for servizio in self.servizi.all())
        return 30  # Default
    
    @property
    def data_ora_prenotazione(self):
        naive_datetime = datetime.combine(self.slot.data, self.slot.ora_inizio)
        return timezone.make_aware(naive_datetime)
    
    @property
    def is_today(self):
        return self.slot.data == timezone.now().date()
    
    @property
    def is_future(self):
        return self.data_ora_prenotazione > timezone.now()
    
    @property
    def can_be_cancelled(self):
        # Può essere cancellata se è futura e non è stata ancora processata
        return (
            self.is_future and 
            self.stato in ['confermata', 'in_attesa'] and
            not self.ordine
        )
    
    @property
    def totale_stimato(self):
        """Calcola il totale stimato della prenotazione"""
        return sum(servizio.prezzo for servizio in self.servizi.all())
    
    def converti_in_ordine(self, operatore=None):
        """Converte la prenotazione in un ordine"""
        if self.ordine:
            return self.ordine
        
        from apps.ordini.models import Ordine, ItemOrdine
        
        # Calcola il totale
        totale = sum(servizio.prezzo for servizio in self.servizi.all())
        
        # Crea l'ordine
        ordine = Ordine.objects.create(
            cliente=self.cliente,
            origine='prenotazione',
            tipo_consegna='immediata',
            ora_consegna_prevista=self.data_ora_prenotazione,
            totale=totale,
            totale_finale=totale,
            stato_pagamento='non_pagato',
            nota=f'Da prenotazione del {self.slot.data.strftime("%d/%m/%Y")} alle {self.slot.ora_inizio.strftime("%H:%M")}',
            tipo_auto=self.tipo_auto,
            operatore=operatore
        )
        
        # Crea gli item dell'ordine
        for servizio in self.servizi.all():
            item = ItemOrdine.objects.create(
                ordine=ordine,
                servizio_prodotto=servizio,
                quantita=1,
                prezzo_unitario=servizio.prezzo
            )
            # Forza il salvataggio per attivare l'auto-assegnazione postazione
            item.save()
            
            # Se l'auto-assegnazione non ha funzionato, assegna alla prima postazione disponibile
            if not item.postazione_assegnata and servizio.tipo == 'servizio':
                from apps.core.models import Postazione
                postazione_default = Postazione.objects.filter(attiva=True).first()
                if postazione_default:
                    item.postazione_assegnata = postazione_default
                    item.save()
        
        # Collega l'ordine alla prenotazione
        self.ordine = ordine
        self.stato = 'completata'
        self.save()
        
        return ordine
    
    def annulla(self, motivo=''):
        """Annulla la prenotazione"""
        if self.can_be_cancelled:
            self.stato = 'annullata'
            if motivo:
                self.nota_interna = f"Annullata: {motivo}"
            self.save()
            
            # Aggiorna i contatori dello slot
            self.slot.aggiorna_contatori()
            return True
        return False
    
    def segna_no_show(self):
        """Segna la prenotazione come no-show"""
        self.stato = 'no_show'
        self.save()
        self.slot.aggiorna_contatori()


class CalendarioPersonalizzato(models.Model):
    """Configurazioni personalizzate del calendario per giorni specifici"""
    data = models.DateField(unique=True)
    chiuso = models.BooleanField(default=False)
    orario_speciale_inizio = models.TimeField(null=True, blank=True)
    orario_speciale_fine = models.TimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    
    class Meta:
        ordering = ['data']
        verbose_name_plural = "Calendario Personalizzato"
    
    def __str__(self):
        if self.chiuso:
            return f"{self.data} - CHIUSO"
        elif self.orario_speciale_inizio:
            return f"{self.data} - Orario speciale {self.orario_speciale_inizio}-{self.orario_speciale_fine}"
        return str(self.data)