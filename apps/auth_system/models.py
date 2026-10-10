from django.contrib.auth.models import Group, User
from django.db import models

from .sezioni import SEZIONI

SEZIONE_CHOICES = [(chiave, nome) for chiave, nome, _, _ in SEZIONI]


class PermessoGruppo(models.Model):
    """Il gruppo puo' accedere alla sezione (vedi sezioni.py)."""
    sezione = models.CharField(max_length=30, choices=SEZIONE_CHOICES)
    gruppo = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='permessi_sezione')

    class Meta:
        unique_together = [('sezione', 'gruppo')]
        verbose_name = 'Permesso di gruppo'
        verbose_name_plural = 'Permessi di gruppo'

    def __str__(self):
        return f'{self.gruppo} -> {self.get_sezione_display()}'


class PermessoUtente(models.Model):
    """Eccezione per un singolo utente: consente o nega la sezione a
    prescindere dai suoi gruppi."""
    sezione = models.CharField(max_length=30, choices=SEZIONE_CHOICES)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='permessi_sezione')
    consenti = models.BooleanField(default=True)

    class Meta:
        unique_together = [('sezione', 'user')]
        verbose_name = 'Eccezione per utente'
        verbose_name_plural = 'Eccezioni per utente'

    def __str__(self):
        return f"{self.user} {'+' if self.consenti else '-'} {self.get_sezione_display()}"
