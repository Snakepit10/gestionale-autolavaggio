from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import MinValueValidator
from decimal import Decimal


class Cassa(models.Model):
    """Anagrafica delle casse gestite dall'autolavaggio."""
    TIPO_CHOICES = [
        ('servito', 'Servito (ordini POS)'),
        ('automatica', 'Cassa automatica'),
    ]
    nome = models.CharField(max_length=100, verbose_name='Nome')
    numero = models.CharField(max_length=20, blank=True, verbose_name='Numero',
        help_text='Numero identificativo della cassa (es. 11057)')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    tracking_washcycles = models.BooleanField(default=False,
        verbose_name='Traccia WashCycles',
        help_text='Abilita il campo WashCycles nel form di chiusura')
    modalita_registratore = models.BooleanField(default=False,
        verbose_name='Modalita registratore (solo totale scontrino)',
        help_text='Se attivo, il form di chiusura mostra solo il totale scontrino (no vendite contante/non contante)')
    attiva = models.BooleanField(default=True)
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Cassa'
        verbose_name_plural = 'Casse'
        ordering = ['ordine', 'nome']

    def __str__(self):
        if self.numero:
            return f"{self.nome} (n. {self.numero})"
        return self.nome


class ChiusuraCassa(models.Model):
    """Gestisce l'apertura e chiusura giornaliera della cassa"""

    STATO_CHOICES = [
        ('aperta', 'Aperta'),
        ('chiusa', 'Chiusa'),
    ]

    # Identificazione
    data = models.DateField(default=timezone.now)
    operatore_apertura = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='aperture_cassa'
    )
    operatore_chiusura = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chiusure_cassa'
    )

    # Apertura
    data_ora_apertura = models.DateTimeField(auto_now_add=True)
    fondo_cassa_iniziale = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Contanti presenti all'apertura"
    )

    # Chiusura
    data_ora_chiusura = models.DateTimeField(null=True, blank=True)
    conteggio_cassa_reale = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Contanti effettivamente presenti alla chiusura"
    )

    # Calcoli automatici (aggiornati in tempo reale)
    totale_incassi_contanti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Totale incassi in contanti della giornata"
    )
    totale_pagamenti_contanti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Totale pagamenti in contanti (fornitori, spese)"
    )
    totale_prelievi = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Prelievi dal fondo cassa"
    )
    totale_versamenti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Versamenti nel fondo cassa"
    )

    # Pagamenti non-contanti (per verifica incrociata)
    totale_carte = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_bancomat = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_bonifici = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_altro = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Note e stato
    note_apertura = models.TextField(blank=True)
    note_chiusura = models.TextField(blank=True, help_text="Giustificazione differenze cassa")
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='aperta')

    # Conferma
    confermata = models.BooleanField(default=False, help_text="Chiusura confermata e bloccata")

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Chiusura Cassa"
        verbose_name_plural = "Chiusure Cassa"
        ordering = ['-data']
        unique_together = ['data']

    def __str__(self):
        return f"Cassa {self.data.strftime('%d/%m/%Y')} - {self.get_stato_display()}"

    @property
    def cassa_teorica_finale(self):
        """Calcola la cassa teorica finale"""
        return (
            self.fondo_cassa_iniziale +
            self.totale_incassi_contanti -
            self.totale_pagamenti_contanti +
            self.totale_versamenti -
            self.totale_prelievi
        )

    @property
    def differenza_cassa(self):
        """Calcola la differenza tra cassa reale e teorica"""
        if self.conteggio_cassa_reale is not None:
            return self.conteggio_cassa_reale - self.cassa_teorica_finale
        return None

    @property
    def stato_differenza(self):
        """Restituisce lo stato della differenza (ok, mancante, eccedente)"""
        diff = self.differenza_cassa
        if diff is None:
            return 'non_chiusa'
        if abs(diff) < Decimal('0.50'):  # Tolleranza 50 centesimi
            return 'ok'
        elif diff < 0:
            return 'mancante'
        else:
            return 'eccedente'

    @property
    def totale_incassi_giornalieri(self):
        """Totale incassi di tutti i metodi"""
        return (
            self.totale_incassi_contanti +
            self.totale_carte +
            self.totale_bancomat +
            self.totale_bonifici +
            self.totale_altro
        )

    def ricalcola_totali(self):
        """Ricalcola tutti i totali dai movimenti e pagamenti"""
        from apps.ordini.models import Pagamento
        from django.db.models import Sum, Q

        # Pagamenti del giorno
        pagamenti = Pagamento.objects.filter(
            data_pagamento__date=self.data
        )

        # Incassi contanti
        self.totale_incassi_contanti = pagamenti.filter(
            metodo='contanti'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        # Altri metodi
        self.totale_carte = pagamenti.filter(
            metodo='carta'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_bancomat = pagamenti.filter(
            metodo='bancomat'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_bonifici = pagamenti.filter(
            metodo='bonifico'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        # Movimenti cassa
        movimenti = self.movimenti.all()

        self.totale_pagamenti_contanti = movimenti.filter(
            tipo='uscita',
            categoria='pagamento'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_prelievi = movimenti.filter(
            tipo='prelievo'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_versamenti = movimenti.filter(
            tipo='versamento'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.save()

    def chiudi(self, conteggio_reale, note='', operatore=None):
        """Chiude la cassa con il conteggio reale"""
        if self.stato == 'chiusa':
            raise ValueError("La cassa è già chiusa")

        self.conteggio_cassa_reale = conteggio_reale
        self.note_chiusura = note
        self.data_ora_chiusura = timezone.now()
        self.operatore_chiusura = operatore
        self.stato = 'chiusa'
        self.save()

    def conferma_chiusura(self):
        """Conferma definitivamente la chiusura (blocco modifiche)"""
        if self.stato != 'chiusa':
            raise ValueError("Devi prima chiudere la cassa")
        self.confermata = True
        self.save()


class MovimentoCassa(models.Model):
    """Traccia i movimenti di cassa non legati a ordini (spese, prelievi, versamenti)"""

    TIPO_CHOICES = [
        ('entrata', 'Entrata'),
        ('uscita', 'Uscita'),
        ('prelievo', 'Prelievo'),
        ('versamento', 'Versamento'),
    ]

    CATEGORIA_CHOICES = [
        ('pagamento', 'Pagamento Fornitore/Spesa'),
        ('prelievo_banca', 'Prelievo per Banca'),
        ('versamento_banca', 'Versamento da Banca'),
        ('fondo_cambio', 'Fondo Cambio'),
        ('altro', 'Altro'),
    ]

    chiusura_cassa = models.ForeignKey(
        ChiusuraCassa,
        on_delete=models.CASCADE,
        related_name='movimenti'
    )

    data_ora = models.DateTimeField(default=timezone.now)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES)

    importo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    causale = models.CharField(max_length=200, help_text="Descrizione del movimento")
    dettagli = models.TextField(blank=True)

    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    # Documenti
    riferimento_documento = models.CharField(
        max_length=100,
        blank=True,
        help_text="Numero fattura, ricevuta, etc."
    )

    creato_il = models.DateTimeField(auto_now_add=True)
    modificato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Movimento Cassa"
        verbose_name_plural = "Movimenti Cassa"
        ordering = ['-data_ora']

    def __str__(self):
        segno = '+' if self.tipo in ['entrata', 'versamento'] else '-'
        return f"{segno}€{self.importo} - {self.causale} ({self.data_ora.strftime('%d/%m/%Y %H:%M')})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Ricalcola i totali della chiusura cassa
        self.chiusura_cassa.ricalcola_totali()


# ---------------------------------------------------------------------------
# Estensione ChiusuraCassa (proprieta aggregate)
# ---------------------------------------------------------------------------

def _num_ordini_giorno(self):
    from apps.ordini.models import Ordine
    return Ordine.objects.filter(data_ora__date=self.data).count()


def _num_washcycles_giorno(self):
    """Conta i washcycles venduti (items con servizio 'washcycle' o simili).
    Euristico: item che hanno un servizio il cui nome contiene 'wash' o 'lavaggio'."""
    from apps.ordini.models import ItemOrdine
    return ItemOrdine.objects.filter(
        ordine__data_ora__date=self.data,
        servizio_prodotto__tipo='servizio',
    ).count()


def _totale_incassi_giorno(self):
    """Somma di tutti i metodi di pagamento del giorno (servito)."""
    return self.totale_incassi_giornalieri


ChiusuraCassa.num_ordini_giorno = property(_num_ordini_giorno)
ChiusuraCassa.num_washcycles_giorno = property(_num_washcycles_giorno)


# ---------------------------------------------------------------------------
# Chiusura giornaliera casse automatiche (cambia gettoni, portali)
# ---------------------------------------------------------------------------

class ChiusuraCassaAutomatica(models.Model):
    """Chiusura giornaliera di una cassa automatica (cambia gettoni, portale blu/azzurro)."""
    cassa = models.ForeignKey(
        Cassa, on_delete=models.PROTECT,
        related_name='chiusure_automatiche',
        limit_choices_to={'tipo': 'automatica'},
    )
    data = models.DateField(default=timezone.now)
    data_ora_chiusura = models.DateTimeField(auto_now_add=True)
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='chiusure_automatiche',
    )

    # Incassi
    incasso_totale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Incasso totale',
    )
    incasso_ricarica = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Incasso ricarica',
    )

    # Vendite
    vendita_contante = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Vendita contante',
    )
    vendita_non_contante = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Vendita non contante',
    )

    # Verifica fisica (opzionale)
    resto_erogato_reale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Resto erogato reale (verifica fisica)',
        help_text='Conteggio reale del resto erogato (per verificare con il teorico)',
    )

    # Conteggio fisico dei contanti presenti nella cassa a fine giornata
    contanti_conteggiati = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Contanti conteggiati (fine giornata)',
        help_text='Somma fisica dei contanti presenti nella cassa a fine giornata',
    )

    # WashCycles (solo per portali)
    wash_cycles = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name='WashCycles',
        help_text='Numero di cicli di lavaggio erogati (solo per i portali)',
    )

    note = models.TextField(blank=True)
    confermata = models.BooleanField(default=False)

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Chiusura cassa automatica'
        verbose_name_plural = 'Chiusure casse automatiche'
        ordering = ['-data', 'cassa__ordine']
        unique_together = [('cassa', 'data')]

    def __str__(self):
        return f"{self.cassa} — {self.data.strftime('%d/%m/%Y')}"

    @property
    def incasso_vendita(self):
        """Incasso dalle vendite = incasso totale - incasso ricarica."""
        return self.incasso_totale - self.incasso_ricarica

    @property
    def vendita_totale(self):
        """Vendita totale = contante + non contante."""
        return self.vendita_contante + self.vendita_non_contante

    @property
    def resto_erogato_teorico(self):
        """Resto erogato teorico = incasso vendita - vendita totale."""
        return self.incasso_vendita - self.vendita_totale

    @property
    def differenza(self):
        """Differenza tra resto erogato reale e teorico."""
        return self.resto_erogato_reale - self.resto_erogato_teorico

    @property
    def stato_differenza(self):
        """Stato della differenza (ok, mancante, eccedente)."""
        diff = self.differenza
        if abs(diff) < Decimal('0.50'):
            return 'ok'
        elif diff < 0:
            return 'mancante'
        else:
            return 'eccedente'

    @property
    def contanti_teorici(self):
        """Contanti teorici: vendita contante - resto erogato."""
        if self.cassa.modalita_registratore:
            return self.incasso_totale
        return self.vendita_contante - self.resto_erogato_teorico


# ---------------------------------------------------------------------------
# Quadratura giornaliera complessiva (scassettamento)
# ---------------------------------------------------------------------------

class QuadraturaGiornaliera(models.Model):
    """
    Quadratura a fine giornata.
    L'operatore scassetta tutte le casse automatiche + il registratore e conta
    TUTTI i contanti insieme + il totale del lettore carte del servito.
    Il totale viene confrontato con la vendita totale self-service + ordini POS pagati.
    """
    data = models.DateField(unique=True, default=timezone.now)

    contanti_totali = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Contanti totali conteggiati',
        help_text='Somma di tutto il contante scassettato dalle casse automatiche e dal registratore',
    )
    lettore_carte_servito = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Totale lettore carte POS servito',
        help_text='Totale riportato dal terminale POS (lettore carte) del servito',
    )
    fondo_cassa_iniziale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Fondo cassa iniziale',
        help_text='Contanti gia presenti in cassa all\'inizio della giornata (verra sottratto dal totale reale)',
    )
    note = models.TextField(blank=True)
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='quadrature_giornaliere',
    )

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Quadratura giornaliera'
        verbose_name_plural = 'Quadrature giornaliere'
        ordering = ['-data']

    def __str__(self):
        return f"Quadratura {self.data.strftime('%d/%m/%Y')}"

    @property
    def totale_reale(self):
        """Totale netto rilevato: contanti + carte - fondo cassa iniziale."""
        return self.contanti_totali + self.lettore_carte_servito - self.fondo_cassa_iniziale
