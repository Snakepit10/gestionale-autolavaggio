from django.conf import settings
from django.db import models


class SetCartellini(models.Model):
    """Un set di cartellini kanban salvato.

    Il campo `configurazione` contiene l'intero stato del generatore
    (libreria prodotti, campi, palette, cards con snapshot, opzioni QR/foto/
    posizione/colore) come prodotto da `currentState()` nel JS del generatore.
    Una sola colonna JSON per mantenere fedelta 1:1 con il file standalone
    e non frammentare in 5 tabelle.

    I set sono condivisi tra tutti gli utenti staff.
    """

    nome = models.CharField(max_length=120)
    descrizione = models.CharField(max_length=300, blank=True, default='')
    configurazione = models.JSONField()
    creato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Set cartellini'
        verbose_name_plural = 'Set cartellini'

    def __str__(self):
        return self.nome

    @property
    def num_cartellini(self):
        try:
            return len(self.configurazione.get('cards', []) or [])
        except (AttributeError, TypeError):
            return 0
