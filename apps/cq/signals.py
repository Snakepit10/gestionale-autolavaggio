from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='cq.SchedaCQ')
def ricalcola_punteggi_scheda(sender, instance, **kwargs):
    """Ricalcola i punteggi ogni volta che la scheda CQ viene salvata."""
    # Importazione lazy per evitare circular import
    from apps.cq.logic import calcola_e_assegna_punteggi
    calcola_e_assegna_punteggi(instance)


@receiver(post_save, sender='cq.DifettoCQ')
def ricalcola_punteggi_difetto(sender, instance, **kwargs):
    """Ricalcola i punteggi quando un difetto viene aggiunto/modificato."""
    from apps.cq.logic import calcola_e_assegna_punteggi
    calcola_e_assegna_punteggi(instance.scheda)
