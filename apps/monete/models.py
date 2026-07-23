"""Monete virtuali: portafoglio clienti + economia dei nodi impianto.

Design notes:
- Il saldo e' UN campo PositiveIntegerField (mai negativo a livello DB),
  non la coppia totali/utilizzati dei punti fedelta': piu' semplice, e lo
  storico completo vive nel ledger MovimentoMoneta con saldo_dopo di audit.
- Ogni variazione di saldo passa dai servizi atomici di
  services/wallet.py (select_for_update + movimento nella stessa
  transazione): MAI modificare SaldoMonete.saldo direttamente.
- La chiave di idempotenza sul movimento (unique parziale) e' la difesa
  contro doppi accrediti da webhook rieseguiti e doppie spese da
  double-tap del form.
"""
from django.conf import settings
from django.db import models


class NodoImpianto(models.Model):
    """Un punto di erogazione comandabile via MQTT (pista, portale...).

    Lo slug e' il segmento del topic MQTT (autolavaggio/<slug>/rpc) e
    deve combaciare con l'MQTT prefix configurato sul dispositivo.
    L'economia e' PER NODO: monete_per_impulso decide quante monete
    costa un singolo impulso gettoniera su questo nodo.
    """
    slug = models.SlugField(
        max_length=50, unique=True,
        help_text="Segmento topic MQTT (es. 'pista2'): deve combaciare "
                  "con l'MQTT prefix autolavaggio/<slug> del dispositivo.")
    nome = models.CharField(max_length=100, help_text="Es. 'Pista 2 self-service'")
    switch_id = models.PositiveSmallIntegerField(
        default=1,
        help_text="Uscita del rele' gettoniera sullo Shelly (pista2: OUT2 = id 1).")
    monete_per_impulso = models.PositiveIntegerField(
        default=1,
        help_text="Quante monete virtuali costa UN impulso su questo nodo.")
    max_impulsi = models.PositiveSmallIntegerField(
        default=10,
        help_text="Tetto di impulsi per singola operazione di avvio.")
    attivo = models.BooleanField(default=True)
    ordine = models.PositiveSmallIntegerField(
        default=0, help_text="Ordinamento nelle liste (0 = primo).")
    note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['ordine', 'slug']
        verbose_name = 'Nodo impianto'
        verbose_name_plural = 'Nodi impianto'

    def __str__(self):
        return f'{self.nome} ({self.slug})'

    def costo_monete(self, impulsi: int) -> int:
        return impulsi * self.monete_per_impulso


class SaldoMonete(models.Model):
    """Saldo monete virtuali di un cliente (una riga per cliente)."""
    cliente = models.OneToOneField(
        'clienti.Cliente', on_delete=models.CASCADE,
        related_name='saldo_monete')
    saldo = models.PositiveIntegerField(default=0)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Saldo monete'
        verbose_name_plural = 'Saldi monete'

    def __str__(self):
        return f'{self.cliente}: {self.saldo} monete'


class PacchettoMonete(models.Model):
    """Pacchetto acquistabile online (es. '10 monete + 2 bonus a 10 EUR')."""
    nome = models.CharField(max_length=100)
    monete = models.PositiveIntegerField()
    bonus = models.PositiveIntegerField(
        default=0, help_text='Monete extra regalate con questo pacchetto.')
    prezzo = models.DecimalField(max_digits=7, decimal_places=2)
    attivo = models.BooleanField(default=True)
    ordine = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordine', 'prezzo']
        verbose_name = 'Pacchetto monete'
        verbose_name_plural = 'Pacchetti monete'

    def __str__(self):
        return f'{self.nome} ({self.monete_totali} monete a {self.prezzo} EUR)'

    @property
    def monete_totali(self) -> int:
        return self.monete + self.bonus


class AcquistoMonete(models.Model):
    """Un acquisto di monete via pagamento online.

    Ciclo di vita: creato -> pagato -> accreditato (il saldo e' stato
    incrementato); oppure annullato/fallito. provider_ref e' l'id della
    Checkout Session Stripe o dell'Order PayPal: unico per provider,
    cosi' un webhook/ritorno rieseguito non puo' creare doppioni.
    """
    PROVIDER_CHOICES = [('stripe', 'Stripe'), ('paypal', 'PayPal')]
    STATO_CHOICES = [
        ('creato', 'Creato'),
        ('pagato', 'Pagato'),
        ('accreditato', 'Accreditato'),
        ('annullato', 'Annullato'),
        ('fallito', 'Fallito'),
    ]

    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.PROTECT,
        related_name='acquisti_monete')
    pacchetto = models.ForeignKey(
        PacchettoMonete, null=True, on_delete=models.SET_NULL)
    # Snapshot al momento dell'acquisto: il pacchetto puo' cambiare dopo
    monete = models.PositiveIntegerField()
    importo = models.DecimalField(max_digits=7, decimal_places=2)
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    provider_ref = models.CharField(
        max_length=255, blank=True, default='', db_index=True,
        help_text='Stripe: id Checkout Session (cs_...); PayPal: order id.')
    stato = models.CharField(max_length=15, choices=STATO_CHOICES, default='creato')
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creato_il']
        verbose_name = 'Acquisto monete'
        verbose_name_plural = 'Acquisti monete'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'provider_ref'],
                name='uq_acquisto_provider_ref',
                condition=~models.Q(provider_ref=''),
            ),
        ]

    def __str__(self):
        return (f'{self.cliente} - {self.monete} monete via '
                f'{self.get_provider_display()} [{self.stato}]')


class MovimentoMoneta(models.Model):
    """Ledger dei movimenti monete (accrediti positivi, addebiti negativi).

    saldo_dopo fotografa il saldo risultante: audit trail completo anche
    se il saldo corrente venisse mai toccato a mano.
    """
    TIPO_CHOICES = [
        ('acquisto_online', 'Acquisto online'),
        ('ricarica_cassa', 'Ricarica in cassa'),
        ('regalo', 'Regalo'),
        ('promozione', 'Promozione'),
        ('lavaggio', 'Avvio lavaggio'),
        ('storno', 'Storno'),
        ('rettifica', 'Rettifica manuale'),
        ('abbonamento', 'Abbonamento'),  # riservato per sviluppi futuri
    ]

    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.CASCADE,
        related_name='movimenti_monete')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    monete = models.IntegerField(
        help_text='Positivo = accredito, negativo = addebito.')
    saldo_dopo = models.PositiveIntegerField()
    descrizione = models.CharField(max_length=200)

    # Riferimenti opzionali per audit
    nodo = models.ForeignKey(
        NodoImpianto, null=True, blank=True, on_delete=models.SET_NULL)
    impulsi = models.PositiveSmallIntegerField(null=True, blank=True)
    acquisto = models.ForeignKey(
        AcquistoMonete, null=True, blank=True, on_delete=models.SET_NULL)
    importo = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        help_text='EUR incassati (per ricarica_cassa): riconciliabile con finanze.')
    operatore = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')

    # Anti doppioni: stessa chiave = stessa operazione logica. Unique
    # parziale (esclude '') cosi' i movimenti senza chiave non collidono.
    chiave_idempotenza = models.CharField(max_length=64, blank=True, default='')
    creato_il = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-creato_il']
        verbose_name = 'Movimento monete'
        verbose_name_plural = 'Movimenti monete'
        indexes = [
            models.Index(fields=['cliente', '-creato_il']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['chiave_idempotenza'],
                name='uq_movimento_idempotenza',
                condition=~models.Q(chiave_idempotenza=''),
            ),
        ]

    def __str__(self):
        segno = '+' if self.monete >= 0 else ''
        return (f'{self.cliente} {segno}{self.monete} '
                f'({self.get_tipo_display()}) -> {self.saldo_dopo}')


class ImpostazioniMonete(models.Model):
    """Singleton di configurazione del modulo (pattern get_solo)."""
    vendita_online_attiva = models.BooleanField(
        default=False,
        help_text='Mostra i pacchetti acquistabili nell\'area cliente.')
    stripe_attivo = models.BooleanField(default=False)
    paypal_attivo = models.BooleanField(default=False)
    cooldown_lavaggio_sec = models.PositiveSmallIntegerField(
        default=15,
        help_text='Secondi minimi tra due avvii dello stesso cliente '
                  'sullo stesso nodo (anti double-tap).')
    testo_pagina_acquisto = models.TextField(
        blank=True, default='',
        help_text='Testo libero mostrato sopra i pacchetti in Le mie monete.')
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Impostazioni monete'
        verbose_name_plural = 'Impostazioni monete'

    def __str__(self):
        return 'Impostazioni monete'

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
