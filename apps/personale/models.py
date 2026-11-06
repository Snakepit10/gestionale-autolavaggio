"""Costo del personale: dipendenti, parametri retributivi, presenze e
mesi di calcolo (come il foglio Excel "Registrazione mensile").

Le presenze sono una riga per dipendente e giorno, indipendenti dal
mese: oggi si scrivono a mano dal calendario, domani le scrivera' la
timbratura (origine 'timbratura') senza cambiare il calcolo."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Dipendente(models.Model):
    TIPO_CHOICES = [
        ('contrattualizzato', 'Contrattualizzato'),
        ('occasionale', 'Occasionale'),
    ]
    nome = models.CharField(max_length=60, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='contrattualizzato')
    attivo = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    ordine = models.PositiveIntegerField(default=0)
    # Account del gestionale: servira' alla timbratura (login + NFC/QR)
    utente = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='dipendente')

    class Meta:
        verbose_name = 'Dipendente'
        verbose_name_plural = 'Dipendenti'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome

    @property
    def contrattualizzato(self):
        return self.tipo == 'contrattualizzato'


class ParametroRetributivo(models.Model):
    """Storico dei parametri: quando un valore cambia si aggiunge una riga
    con la data da cui vale, cosi' i mesi passati non cambiano."""
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='parametri')
    valido_dal = models.DateField()
    tariffa_oraria = models.DecimalField(max_digits=7, decimal_places=2,
                                         validators=[MinValueValidator(Decimal('0'))])
    coefficiente_lordo = models.DecimalField(max_digits=5, decimal_places=3, default=Decimal('1'),
                                             validators=[MinValueValidator(Decimal('1'))])
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'Parametro retributivo'
        verbose_name_plural = 'Parametri retributivi'
        ordering = ['dipendente', '-valido_dal']
        unique_together = [('dipendente', 'valido_dal')]

    def __str__(self):
        return f'{self.dipendente} dal {self.valido_dal:%d/%m/%Y}: €{self.tariffa_oraria}/h × {self.coefficiente_lordo}'


class MesePersonale(models.Model):
    mese = models.DateField(unique=True, help_text='Primo giorno del mese')
    ore_giorno_ferie = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('8'))
    ore_giorno_malattia = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('8'))
    note = models.TextField(blank=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    aggiornato_da = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')

    class Meta:
        verbose_name = 'Mese del personale'
        verbose_name_plural = 'Mesi del personale'
        ordering = ['-mese']

    def __str__(self):
        return f'Personale {self.mese:%m/%Y}'


class RigaMese(models.Model):
    """Dipendente presente nel calcolo del mese, con i valori da
    compilare (le celle gialle dell'Excel)."""
    mese = models.ForeignKey(MesePersonale, on_delete=models.CASCADE, related_name='righe')
    dipendente = models.ForeignKey(Dipendente, on_delete=models.PROTECT, related_name='righe_mese')
    ordine = models.PositiveIntegerField(default=0)
    # vuoto = quello dei parametri in vigore nel mese
    coefficiente_lordo = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True,
                                             validators=[MinValueValidator(Decimal('1'))])
    busta_paga = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True,
                                     validators=[MinValueValidator(Decimal('0'))])
    bonus = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    pagato = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True,
                                 validators=[MinValueValidator(Decimal('0'))])

    class Meta:
        verbose_name = 'Riga del mese'
        verbose_name_plural = 'Righe del mese'
        ordering = ['ordine', 'dipendente__ordine', 'dipendente__nome']
        unique_together = [('mese', 'dipendente')]

    def __str__(self):
        return f'{self.dipendente} {self.mese.mese:%m/%Y}'


class Presenza(models.Model):
    ASSENZA_CHOICES = [
        ('', '—'),
        ('F', 'Ferie'),
        ('M', 'Malattia'),
        ('L', 'Libero'),
    ]
    ORIGINE_CHOICES = [
        ('manuale', 'Manuale'),
        ('timbratura', 'Timbratura'),
    ]
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='presenze')
    data = models.DateField()
    ore = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
                              validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('16'))])
    assenza = models.CharField(max_length=1, choices=ASSENZA_CHOICES, blank=True)
    origine = models.CharField(max_length=12, choices=ORIGINE_CHOICES, default='manuale')

    class Meta:
        verbose_name = 'Presenza'
        verbose_name_plural = 'Presenze'
        ordering = ['data']
        unique_together = [('dipendente', 'data')]

    def __str__(self):
        return f'{self.dipendente} {self.data:%d/%m/%Y} {self.assenza or self.ore}'

    @property
    def valore(self):
        """Come nella cella del calendario: ore oppure F/M/L."""
        if self.assenza:
            return self.assenza
        return '' if self.ore is None else self.ore


class PrevisioneSquadra(models.Model):
    """La previsione del giorno, salvata la sera prima (dalle 19), per
    confrontarla poi con i lavaggi e le presenze reali."""
    data = models.DateField(unique=True)
    lavaggi_previsti = models.DecimalField(max_digits=6, decimal_places=1)
    lavaggi_alti = models.DecimalField(max_digits=6, decimal_places=1)
    squadra = models.PositiveIntegerField()
    pioggia_mm = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    chiuso = models.BooleanField(default=False)
    aggiornata_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Previsione squadra'
        verbose_name_plural = 'Previsioni squadra'
        ordering = ['-data']

    def __str__(self):
        return f'{self.data:%d/%m/%Y}: {self.squadra} persone'


class ParametriPrevisione(models.Model):
    """Parametri della previsione squadra (una sola riga)."""
    lavaggi_per_persona = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal('7'),
        help_text='Lavaggi del servito che una persona fa in una giornata (20 lavaggi / 3 persone = circa 7)')
    margine_pct = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal('10'),
        help_text='Quanto aggiungere ai lavaggi previsti prima di calcolare la squadra')
    aggiornati_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Parametri previsione squadra'
        verbose_name_plural = 'Parametri previsione squadra'

    @classmethod
    def attuali(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def lavaggi_per_persona_f(self):
        return float(self.lavaggi_per_persona) if self.lavaggi_per_persona else None

    @property
    def margine_f(self):
        return float(self.margine_pct) / 100
