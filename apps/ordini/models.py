from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
import uuid
from datetime import datetime, time


class Ordine(models.Model):
    STATO_CHOICES = [
        ('in_attesa', 'In Attesa'),
        ('in_lavorazione', 'In Lavorazione'),
        ('completato', 'Completato'),
        ('annullato', 'Annullato'),
    ]
    
    STATO_PAGAMENTO_CHOICES = [
        ('pagato', 'Pagato'),
        ('non_pagato', 'Non Pagato'),
        ('parziale', 'Pagamento Parziale'),
        ('differito', 'Pagamento Differito'),
    ]
    
    ORIGINE_CHOICES = [
        ('operatore', 'Operatore'),
        ('online', 'Online'),
        ('app', 'App Mobile'),
        ('totem', 'Totem Self-Service'),
        ('prenotazione', 'Prenotazione Web'),
        ('shop', 'Shop Online'),
        ('abbonamento', 'Abbonamento'),
    ]
    
    TIPO_CONSEGNA_CHOICES = [
        ('immediata', 'Consegna Immediata'),
        ('programmata', 'Consegna Programmata'),
    ]
    
    numero_progressivo = models.CharField(max_length=20, unique=True, blank=True)
    data_ora = models.DateTimeField(auto_now_add=True)
    cliente = models.ForeignKey(
        'clienti.Cliente', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    origine = models.CharField(max_length=20, choices=ORIGINE_CHOICES, default='operatore')
    
    # Consegna
    tipo_consegna = models.CharField(max_length=20, choices=TIPO_CONSEGNA_CHOICES, default='immediata')
    ora_consegna_richiesta = models.TimeField(null=True, blank=True)
    ora_consegna_prevista = models.DateTimeField(null=True, blank=True)
    tempo_attesa_minuti = models.IntegerField(default=0)

    # Pianificazione
    durata_stimata_minuti = models.IntegerField(
        default=0,
        help_text="Durata totale stimata per completare l'ordine (calcolata o modificata manualmente)"
    )
    durata_modificata_manualmente = models.BooleanField(
        default=False,
        help_text="True se l'operatore ha modificato manualmente la durata"
    )
    
    # Totali
    totale = models.DecimalField(max_digits=10, decimal_places=2)
    sconto_applicato = models.ForeignKey(
        'core.Sconto', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    importo_sconto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_finale = models.DecimalField(max_digits=10, decimal_places=2)
    punti_fedelta_generati = models.IntegerField(default=0)
    
    # Pagamento
    stato_pagamento = models.CharField(max_length=20, choices=STATO_PAGAMENTO_CHOICES, default='non_pagato')
    importo_pagato = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    metodo_pagamento = models.CharField(max_length=50, blank=True)
    data_scadenza_pagamento = models.DateField(null=True, blank=True)
    
    nota = models.TextField(blank=True)
    tipo_auto = models.CharField(max_length=200, blank=True, help_text="Modello e colore dell'auto")
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='in_attesa')
    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    # Ritiro auto
    auto_ritirata = models.BooleanField(default=False, help_text="Indica se l'auto è stata ritirata dal cliente")
    data_ritiro = models.DateTimeField(null=True, blank=True, help_text="Data e ora del ritiro")

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-data_ora']
        verbose_name_plural = "Ordini"
    
    def __str__(self):
        return f"Ordine {self.numero_progressivo} - {self.cliente or 'Anonimo'}"
    
    def save(self, *args, **kwargs):
        if not self.numero_progressivo:
            self.numero_progressivo = self.genera_numero_progressivo()
        super().save(*args, **kwargs)
    
    def genera_numero_progressivo(self):
        oggi = timezone.now().date()
        prefisso = oggi.strftime('%Y%m%d')
        
        ultimo_ordine = Ordine.objects.filter(
            numero_progressivo__startswith=prefisso
        ).order_by('-numero_progressivo').first()
        
        if ultimo_ordine:
            ultimo_numero = int(ultimo_ordine.numero_progressivo[-4:])
            nuovo_numero = ultimo_numero + 1
        else:
            nuovo_numero = 1
        
        return f"{prefisso}-{nuovo_numero:04d}"
    
    @property
    def numero_breve(self):
        """Restituisce solo la parte numerica del numero progressivo senza zeri iniziali
        Es: da '20260307-0031' restituisce 31
        """
        if self.numero_progressivo and '-' in self.numero_progressivo:
            return int(self.numero_progressivo.split('-')[1])
        return 0

    def calcola_durata_da_servizi(self):
        """Calcola durata totale sommando durate dei servizi nell'ordine"""
        durata_totale = 0
        for item in self.items.all():
            if item.servizio_prodotto.tipo == 'servizio':
                durata_totale += item.servizio_prodotto.durata_minuti * item.quantita
        return durata_totale

    def aggiorna_durata_stimata(self, forza_ricalcolo=False):
        """Aggiorna durata stimata se non modificata manualmente"""
        if not self.durata_modificata_manualmente or forza_ricalcolo:
            self.durata_stimata_minuti = self.calcola_durata_da_servizi()
            if forza_ricalcolo:
                self.durata_modificata_manualmente = False
            self.save(update_fields=['durata_stimata_minuti', 'durata_modificata_manualmente'])

    @property
    def ora_fine_prevista(self):
        """Calcola ora di fine basandosi su ora_consegna_prevista + durata"""
        if self.ora_consegna_prevista and self.durata_stimata_minuti:
            from datetime import timedelta
            return self.ora_consegna_prevista + timedelta(minutes=self.durata_stimata_minuti)
        return None

    @property
    def saldo_dovuto(self):
        from decimal import Decimal
        # Converti entrambi a Decimal per evitare errori di tipo
        totale_finale_decimal = Decimal(str(self.totale_finale))
        importo_pagato_decimal = Decimal(str(self.importo_pagato))
        return totale_finale_decimal - importo_pagato_decimal

    @property
    def is_pagato(self):
        return self.saldo_dovuto <= 0
    
    def aggiorna_stato_pagamento(self):
        if self.saldo_dovuto <= 0:
            self.stato_pagamento = 'pagato'
        elif self.importo_pagato > 0:
            self.stato_pagamento = 'parziale'
        else:
            self.stato_pagamento = 'non_pagato'
        self.save(update_fields=['stato_pagamento'])
    
    def calcola_punti_fedelta(self):
        # 1 punto ogni 10 euro di spesa
        return int(self.totale_finale / 10)
    
    def get_items_per_postazione(self):
        items_dict = {}
        for item in self.items.all():
            if item.postazione_assegnata:
                postazione = item.postazione_assegnata
                if postazione not in items_dict:
                    items_dict[postazione] = []
                items_dict[postazione].append(item)
        return items_dict

    def get_stati_postazioni(self):
        """Restituisce [{sigla, stato, nome}] per ogni postazione dell'ordine."""
        risultato = {}
        for item in self.items.all():
            if not item.postazione_assegnata:
                continue
            nome = item.postazione_assegnata.nome
            if nome not in risultato:
                # Sigla: prime lettere di ogni parola (max 3 char)
                sigla = ''.join(w[0].upper() for w in nome.split()[:3])
                risultato[nome] = {'sigla': sigla, 'stato': 'completato', 'nome': nome}
            # Priorita: in_lavorazione > in_attesa > completato
            cur = risultato[nome]['stato']
            if item.stato == 'in_lavorazione':
                risultato[nome]['stato'] = 'in_lavorazione'
            elif item.stato == 'in_attesa' and cur != 'in_lavorazione':
                risultato[nome]['stato'] = 'in_attesa'
        return list(risultato.values())


class ItemOrdine(models.Model):
    STATO_CHOICES = [
        ('in_attesa', 'In Attesa'),
        ('in_lavorazione', 'In Lavorazione'),
        ('completato', 'Completato'),
    ]
    
    ordine = models.ForeignKey(Ordine, related_name='items', on_delete=models.CASCADE)
    servizio_prodotto = models.ForeignKey('core.ServizioProdotto', on_delete=models.PROTECT)
    quantita = models.IntegerField(default=1)
    prezzo_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    postazione_assegnata = models.ForeignKey(
        'core.Postazione', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    
    # Stati per tracking nelle postazioni
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='in_attesa')
    inizio_lavorazione = models.DateTimeField(null=True, blank=True)
    fine_lavorazione = models.DateTimeField(null=True, blank=True)

    # Tracking operatore che ha aggiunto l'item (per evidenziare in lista ordini)
    aggiunto_da = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='items_aggiunti', verbose_name='Aggiunto da operatore',
    )
    
    class Meta:
        ordering = ['postazione_assegnata', 'id']
        verbose_name_plural = "Item Ordine"
    
    def __str__(self):
        return f"{self.servizio_prodotto.titolo} x{self.quantita}"
    
    @property
    def subtotale(self):
        return self.prezzo_unitario * self.quantita
    
    @property
    def durata_lavorazione(self):
        if self.inizio_lavorazione and self.fine_lavorazione:
            delta = self.fine_lavorazione - self.inizio_lavorazione
            return delta.total_seconds() / 60  # minuti
        return None
    
    def inizia_lavorazione(self):
        self.stato = 'in_lavorazione'
        self.inizio_lavorazione = timezone.now()
        self.save()
    
    def completa_lavorazione(self):
        self.stato = 'completato'
        self.fine_lavorazione = timezone.now()
        self.save()
        
        # Verifica se tutto l'ordine è completato
        ordine = self.ordine
        if all(item.stato == 'completato' for item in ordine.items.all()):
            ordine.stato = 'completato'
            ordine.save()
    
    def ricalcola_postazione(self):
        """
        Ricalcola e aggiorna l'assegnazione della postazione basandosi sulla configurazione attuale del servizio.
        Utile quando la configurazione delle postazioni del servizio viene modificata.
        """
        if self.servizio_prodotto.tipo == 'servizio':
            postazioni_disponibili = self.servizio_prodotto.postazioni.filter(attiva=True)
            if postazioni_disponibili.exists():
                # Assegna alla postazione con meno carico
                nuova_postazione = min(
                    postazioni_disponibili,
                    key=lambda p: p.get_ordini_in_coda().count()
                )
                if self.postazione_assegnata != nuova_postazione:
                    vecchia_postazione = self.postazione_assegnata
                    self.postazione_assegnata = nuova_postazione
                    self.save()
                    return f"Item {self.id} spostato da {vecchia_postazione} a {nuova_postazione}"
        return "Nessun cambiamento necessario"
    
    def save(self, *args, **kwargs):
        # Auto-assegna postazione se è un servizio
        if not self.postazione_assegnata and self.servizio_prodotto.tipo == 'servizio':
            postazioni_disponibili = self.servizio_prodotto.postazioni.filter(attiva=True)
            if postazioni_disponibili.exists():
                # Assegna alla postazione con meno carico
                self.postazione_assegnata = min(
                    postazioni_disponibili,
                    key=lambda p: p.get_ordini_in_coda().count()
                )
        
        # Se è un prodotto, imposta lo stato come completato fin dall'inizio
        if self.servizio_prodotto.tipo == 'prodotto' and not self.pk:
            self.stato = 'completato'
            self.fine_lavorazione = timezone.now()
        
        super().save(*args, **kwargs)
        
        # Verifica se tutto l'ordine è completato dopo il salvataggio
        if self.stato == 'completato':
            ordine = self.ordine
            if all(item.stato == 'completato' for item in ordine.items.all()):
                ordine.stato = 'completato'
                ordine.save()


class Pagamento(models.Model):
    METODO_CHOICES = [
        ('contanti', 'Contanti'),
        ('carta', 'Carta di Credito/Debito'),
        ('bancomat', 'Bancomat'),
        ('bonifico', 'Bonifico'),
        ('assegno', 'Assegno'),
        ('abbonamento', 'Abbonamento'),
    ]
    
    ordine = models.ForeignKey(Ordine, related_name='pagamenti', on_delete=models.CASCADE)
    importo = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES)
    data_pagamento = models.DateTimeField(auto_now_add=True)
    riferimento = models.CharField(max_length=100, blank=True, help_text="Num. transazione, assegno, etc.")
    nota = models.TextField(blank=True)
    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        ordering = ['-data_pagamento']
        verbose_name_plural = "Pagamenti"
    
    def __str__(self):
        return f"Pagamento {self.ordine.numero_progressivo} - €{self.importo}"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # L'aggiornamento dello stato dell'ordine è gestito dal signal post_save


class ConfigurazionePianificazione(models.Model):
    """Configurazione orari di lavoro (singleton)"""

    # Orari giornalieri
    ora_inizio = models.TimeField(default=time(8, 0))
    ora_fine = models.TimeField(default=time(19, 0))

    # Pausa pranzo (opzionale)
    pausa_pranzo_attiva = models.BooleanField(default=True)
    ora_inizio_pausa = models.TimeField(default=time(13, 0))
    ora_fine_pausa = models.TimeField(default=time(15, 0))

    # Metadata
    aggiornato_il = models.DateTimeField(auto_now=True)
    aggiornato_da = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Configurazione Pianificazione"
        verbose_name_plural = "Configurazione Pianificazione"

    def __str__(self):
        return f"Orari: {self.ora_inizio.strftime('%H:%M')} - {self.ora_fine.strftime('%H:%M')}"

    def save(self, *args, **kwargs):
        # Enforce singleton
        if not self.pk and ConfigurazionePianificazione.objects.exists():
            raise ValidationError("Configurazione già esistente")
        return super().save(*args, **kwargs)

    @classmethod
    def get_configurazione(cls):
        config, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'ora_inizio': time(8, 0),
                'ora_fine': time(19, 0),
                'pausa_pranzo_attiva': True,
                'ora_inizio_pausa': time(13, 0),
                'ora_fine_pausa': time(15, 0),
            }
        )
        return config