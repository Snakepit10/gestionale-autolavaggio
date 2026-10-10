from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import ServizioProdotto


@receiver(post_save, sender=ServizioProdotto)
def collega_articolo(sender, instance, created, raw=False, **kwargs):
    """Ogni prodotto del catalogo ha il suo articolo di magazzino."""
    if raw or instance.tipo != 'prodotto':
        return
    from .services import articolo_del_prodotto

    articolo_del_prodotto(instance)
