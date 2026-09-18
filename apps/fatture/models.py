import re
from decimal import Decimal

from django.conf import settings
from django.db import models


class Fattura(models.Model):
    """Raggruppamento di ordini da fatturare a un cliente.

    Il numero e' testo libero (formato consigliato 'N/ANNO'): niente
    vincolo di unicita', solo un avviso soft in creazione se esiste
    gia'. La ragione sociale e' uno snapshot editabile: parte da
    quella del cliente ma puo' essere diversa e sopravvive alla
    cancellazione del cliente (FK SET_NULL).
    """

    STATO_CHOICES = [
        ('da_pagare', 'Da pagare'),
        ('pagata', 'Pagata'),
        ('archiviata', 'Archiviata'),
    ]

    numero = models.CharField(max_length=50)
    data = models.DateField()
    cliente = models.ForeignKey(
        'clienti.Cliente', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='fatture')
    ragione_sociale = models.CharField(max_length=200)
    stato = models.CharField(
        max_length=20, choices=STATO_CHOICES, default='da_pagare')
    nota = models.TextField(blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)
    creata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='fatture_create')
    pagata_il = models.DateTimeField(null=True, blank=True)
    archiviata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-data', '-id']
        verbose_name = 'Fattura'
        verbose_name_plural = 'Fatture'

    def __str__(self):
        return f'Fattura {self.numero} - {self.ragione_sociale}'

    # --- Totali (ordini collegati + righe manuali) -----------------

    @property
    def totale_ordini(self):
        return sum((o.importo_in_fattura for o in self.ordini.all()),
                   Decimal('0'))

    @property
    def totale_righe(self):
        return sum((r.importo or Decimal('0') for r in self.righe.all()),
                   Decimal('0'))

    @property
    def totale(self):
        return self.totale_ordini + self.totale_righe

    @property
    def totale_pagato(self):
        return sum((o.importo_pagato or Decimal('0') for o in self.ordini.all()),
                   Decimal('0'))

    @property
    def saldo_dovuto(self):
        # Le righe manuali non hanno pagamenti tracciati: una fattura
        # segnata pagata/archiviata e' saldata per definizione.
        if self.stato in ('pagata', 'archiviata'):
            return Decimal('0')
        return self.totale - self.totale_pagato

    @property
    def tutti_ordini_pagati(self):
        ordini = list(self.ordini.all())
        return bool(ordini) and all(o.is_pagato for o in ordini)

    # --- Numerazione suggerita -------------------------------------



    @classmethod
    def suggerisci_numero(cls, anno):
        """Prossimo progressivo 'N/ANNO' guardando i numeri esistenti.

        I numeri fuori formato (testo libero) vengono ignorati.
        """
        pattern = re.compile(r'^(\d+)/%d$' % anno)
        massimo = 0
        for numero in cls.objects.values_list('numero', flat=True):
            m = pattern.match(numero.strip())
            if m:
                massimo = max(massimo, int(m.group(1)))
        return f'{massimo + 1}/{anno}'


class RigaFattura(models.Model):
    """Riga manuale di una fattura: voce libera (data, descrizione,
    importo) non legata a un ordine del gestionale."""

    fattura = models.ForeignKey(
        Fattura, on_delete=models.CASCADE, related_name='righe')
    data = models.DateField()
    descrizione = models.CharField(max_length=200)
    importo = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['data', 'id']
        verbose_name = 'Riga fattura'
        verbose_name_plural = 'Righe fattura'

    def __str__(self):
        return f'{self.descrizione} ({self.importo})'
