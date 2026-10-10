from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import ServizioProdotto


@receiver(post_save, sender=ServizioProdotto)
def collega_articolo(sender, instance, created, raw=False, **kwargs):
    """Ogni prodotto del catalogo ha il suo articolo di magazzino."""
    if raw or instance.tipo != 'prodotto' or getattr(instance, '_da_magazzino', False):
        return
    from .models import Articolo
    from .services import articolo_del_prodotto

    # Modificato dal catalogo: l'articolo prende nome, sottocategoria e stato
    # (un prodotto attivo in cassa e' un articolo in vendita)
    collegato = Articolo.objects.filter(prodotto=instance)
    collegato.update(nome=instance.titolo, categoria=instance.gruppo, attivo=instance.attivo)
    if instance.attivo:
        collegato.exclude(tipo='vendita').update(tipo='vendita')
    articolo_del_prodotto(instance)
