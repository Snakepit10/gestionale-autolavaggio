"""Magazzino: articoli, movimenti, fornitori, ordini ai fornitori, consegne
e assegnazioni di merce a operatori e postazioni.

La quantita' in magazzino di un articolo cambia solo tramite
`services.movimenta`, che scrive anche il Movimento e copia la quantita'
nel prodotto del catalogo collegato (cassa e ordini leggono quella).
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Fornitore(models.Model):
    ragione_sociale = models.CharField(max_length=150)
    partita_iva = models.CharField('P.IVA', max_length=20, blank=True)
    referente = models.CharField(max_length=100, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    note = models.TextField(blank=True)
    attivo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Fornitore'
        verbose_name_plural = 'Fornitori'
        ordering = ['ragione_sociale']

    def __str__(self):
        return self.ragione_sociale


class Articolo(models.Model):
    TIPO_CHOICES = [
        ('vendita', 'Prodotto in vendita'),
        ('consumabile', 'Consumabile'),
        ('strumento', 'Strumento'),
    ]
    nome = models.CharField(max_length=150)
    codice = models.CharField(max_length=50, blank=True)
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default='consumabile')
    categoria = models.CharField(max_length=60, blank=True)
    unita = models.CharField('Unità di misura', max_length=20, default='pz')
    quantita = models.IntegerField('Quantità in magazzino', default=0)
    scorta_minima = models.IntegerField(default=0)
    traccia_scorte = models.BooleanField(
        default=True, help_text='Se spento la quantità non viene controllata (illimitata)')
    costo = models.DecimalField('Costo unitario', max_digits=10, decimal_places=2, default=Decimal('0'))
    fornitore = models.ForeignKey(Fornitore, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='articoli', verbose_name='Fornitore abituale')
    prodotto = models.OneToOneField(
        'core.ServizioProdotto', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='articolo', verbose_name='Prodotto del catalogo',
        help_text='La vendita in cassa di questo prodotto scarica il magazzino')
    attivo = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Articolo'
        verbose_name_plural = 'Articoli'
        ordering = ['nome']

    def __str__(self):
        return self.nome

    @property
    def sotto_scorta(self):
        return self.traccia_scorte and self.quantita <= self.scorta_minima

    @property
    def valore(self):
        return self.costo * max(self.quantita, 0) if self.traccia_scorte else Decimal('0')

    @property
    def assegnati(self):
        return (self.assegnazioni.filter(stato__in=Assegnazione.APERTI)
                .aggregate(t=Sum('quantita'))['t'] or 0)


class Movimento(models.Model):
    TIPO_CHOICES = [
        ('carico', 'Carico'),
        ('consegna', 'Consegna fornitore'),
        ('vendita', 'Vendita'),
        ('assegnazione', 'Assegnazione'),
        ('rientro', 'Rientro'),
        ('scarto', 'Scarto'),
        ('rettifica', 'Rettifica inventario'),
    ]
    articolo = models.ForeignKey(Articolo, on_delete=models.CASCADE, related_name='movimenti')
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES)
    quantita = models.IntegerField(help_text='Positivo = entra in magazzino, negativo = esce')
    quantita_prima = models.IntegerField()
    quantita_dopo = models.IntegerField()
    ordine = models.ForeignKey('ordini.Ordine', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='movimenti_magazzino')
    consegna = models.ForeignKey('Consegna', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='movimenti')
    assegnazione = models.ForeignKey('Assegnazione', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='movimenti')
    nota = models.CharField(max_length=255, blank=True)
    operatore = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='movimenti_magazzino')
    data = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Movimento di magazzino'
        verbose_name_plural = 'Movimenti di magazzino'
        ordering = ['-data', '-pk']

    def __str__(self):
        return f'{self.articolo} {self.quantita:+d} ({self.get_tipo_display()})'


class OrdineFornitore(models.Model):
    STATO_CHOICES = [
        ('bozza', 'Bozza'),
        ('inviato', 'Inviato'),
        ('parziale', 'Consegnato in parte'),
        ('evaso', 'Evaso'),
        ('annullato', 'Annullato'),
    ]
    numero = models.CharField(max_length=20, unique=True, editable=False)
    fornitore = models.ForeignKey(Fornitore, on_delete=models.PROTECT, related_name='ordini')
    data = models.DateField(default=timezone.localdate)
    stato = models.CharField(max_length=10, choices=STATO_CHOICES, default='bozza')
    note = models.TextField(blank=True)
    creato_da = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='ordini_fornitori')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ordine fornitore'
        verbose_name_plural = 'Ordini fornitori'
        ordering = ['-data', '-pk']

    def __str__(self):
        return f'{self.numero} {self.fornitore}'

    def save(self, *args, **kwargs):
        if not self.numero:
            anno = (self.data or timezone.localdate()).year
            prefisso = f'OF-{anno}-'
            ultimi = (OrdineFornitore.objects.filter(numero__startswith=prefisso)
                      .values_list('numero', flat=True))
            n = max((int(x.rsplit('-', 1)[1]) for x in ultimi), default=0) + 1
            self.numero = f'{prefisso}{n:03d}'
        super().save(*args, **kwargs)

    @property
    def totale(self):
        return sum((r.importo for r in self.righe.all()), Decimal('0'))

    @property
    def modificabile(self):
        return self.stato in ('bozza', 'inviato')

    def aggiorna_stato(self):
        """Dopo una consegna: evaso se tutte le righe sono ricevute,
        parziale se qualcosa e' arrivato."""
        if self.stato in ('bozza', 'annullato'):
            return
        righe = list(self.righe.all())
        if righe and all(r.residuo <= 0 for r in righe):
            stato = 'evaso'
        elif any(r.ricevuto > 0 for r in righe):
            stato = 'parziale'
        else:
            stato = 'inviato'
        if stato != self.stato:
            self.stato = stato
            self.save(update_fields=['stato'])


class RigaOrdineFornitore(models.Model):
    ordine = models.ForeignKey(OrdineFornitore, on_delete=models.CASCADE, related_name='righe')
    articolo = models.ForeignKey(Articolo, on_delete=models.PROTECT, related_name='righe_ordine')
    quantita = models.PositiveIntegerField()
    prezzo = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    class Meta:
        ordering = ['pk']

    @property
    def importo(self):
        return self.prezzo * self.quantita

    @property
    def ricevuto(self):
        return self.righe_consegna.aggregate(t=Sum('quantita'))['t'] or 0

    @property
    def residuo(self):
        return self.quantita - self.ricevuto


class Consegna(models.Model):
    fornitore = models.ForeignKey(Fornitore, on_delete=models.PROTECT, related_name='consegne')
    ordine = models.ForeignKey(OrdineFornitore, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='consegne')
    data = models.DateField(default=timezone.localdate)
    numero_ddt = models.CharField('N° DDT / bolla', max_length=50, blank=True)
    note = models.TextField(blank=True)
    registrata_da = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='consegne_registrate')
    registrata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Consegna'
        verbose_name_plural = 'Consegne'
        ordering = ['-data', '-pk']

    def __str__(self):
        return f'Consegna {self.fornitore} {self.data:%d/%m/%Y}'

    @property
    def totale(self):
        return sum((r.prezzo * r.quantita for r in self.righe.all()), Decimal('0'))


class RigaConsegna(models.Model):
    consegna = models.ForeignKey(Consegna, on_delete=models.CASCADE, related_name='righe')
    articolo = models.ForeignKey(Articolo, on_delete=models.PROTECT, related_name='righe_consegna')
    riga_ordine = models.ForeignKey(RigaOrdineFornitore, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='righe_consegna')
    quantita = models.PositiveIntegerField()
    prezzo = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    class Meta:
        ordering = ['pk']


class Assegnazione(models.Model):
    STATO_CHOICES = [
        ('in_uso', 'In uso'),
        ('usurato', 'Usurato / da sostituire'),
        ('rotto', 'Rotto'),
        ('smarrito', 'Smarrito'),
        ('esaurito', 'Esaurito'),
        ('restituito', 'Restituito'),
        ('sostituito', 'Sostituito'),
    ]
    # La merce e' ancora presso il destinatario
    APERTI = ('in_uso', 'usurato')
    # Stati che l'operatore puo' segnalare
    SEGNALABILI = ('usurato', 'rotto', 'smarrito', 'esaurito')

    articolo = models.ForeignKey(Articolo, on_delete=models.PROTECT, related_name='assegnazioni')
    quantita = models.PositiveIntegerField(default=1)
    utente = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT,
                               related_name='assegnazioni_magazzino')
    postazione = models.ForeignKey('cq.PostazioneCQ', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='assegnazioni_magazzino')
    stato = models.CharField(max_length=12, choices=STATO_CHOICES, default='in_uso')
    in_apertura = models.BooleanField('Controlla a inizio turno', default=True)
    in_chiusura = models.BooleanField('Controlla a fine turno', default=True)
    data = models.DateTimeField(default=timezone.now)
    assegnata_da = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='assegnazioni_fatte')
    note = models.CharField(max_length=255, blank=True)
    # Segnalazione dell'operatore ancora da vedere in magazzino
    da_gestire = models.BooleanField(default=False)
    chiusa_il = models.DateTimeField(null=True, blank=True)
    origine = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='derivate',
                                help_text='Assegnazione da cui e\' stata divisa o che sostituisce')

    class Meta:
        verbose_name = 'Assegnazione'
        verbose_name_plural = 'Assegnazioni'
        ordering = ['-data', '-pk']

    def __str__(self):
        return f'{self.articolo} ×{self.quantita} → {self.destinatario}'

    @property
    def destinatario(self):
        if self.postazione_id:
            return self.postazione.nome
        if self.utente_id:
            return self.utente.get_full_name() or self.utente.username
        return '—'

    @property
    def aperta(self):
        return self.stato in self.APERTI

    @property
    def colore_stato(self):
        return {'in_uso': 'success', 'usurato': 'warning', 'rotto': 'danger',
                'smarrito': 'danger', 'esaurito': 'secondary', 'restituito': 'info',
                'sostituito': 'secondary'}[self.stato]


class StatoAssegnazione(models.Model):
    ORIGINE_CHOICES = [
        ('magazzino', 'Magazzino'),
        ('operatore', 'Operatore'),
        ('checklist', 'Checklist turno'),
    ]
    assegnazione = models.ForeignKey(Assegnazione, on_delete=models.CASCADE, related_name='storico')
    da_stato = models.CharField(max_length=12, blank=True)
    a_stato = models.CharField(max_length=12)
    quantita = models.PositiveIntegerField()
    origine = models.CharField(max_length=10, choices=ORIGINE_CHOICES, default='magazzino')
    nota = models.CharField(max_length=255, blank=True)
    utente = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='stati_assegnazioni')
    data = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-data', '-pk']

    @property
    def a_stato_display(self):
        return dict(Assegnazione.STATO_CHOICES).get(self.a_stato, self.a_stato)

    @property
    def da_stato_display(self):
        return dict(Assegnazione.STATO_CHOICES).get(self.da_stato, self.da_stato)
