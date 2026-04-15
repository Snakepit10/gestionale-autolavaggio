"""
Popola ItemOrdine.postazione_cq dagli ItemOrdine.postazione_assegnata esistenti,
usando il mapping PostazioneCQ.postazione_fisica.
Se non c'e mapping, assegna la prima PostazioneCQ attiva.
"""
from django.db import migrations


def popola_postazione_cq(apps, schema_editor):
    ItemOrdine = apps.get_model('ordini', 'ItemOrdine')
    PostazioneCQ = apps.get_model('cq', 'PostazioneCQ')

    # Costruisci mapping postazione_fisica_id → PostazioneCQ
    mapping = {}
    for pcq in PostazioneCQ.objects.filter(attiva=True, postazione_fisica__isnull=False):
        mapping[pcq.postazione_fisica_id] = pcq

    # Fallback: prima PostazioneCQ attiva
    fallback = PostazioneCQ.objects.filter(attiva=True).order_by('ordine').first()

    updated = 0
    for item in ItemOrdine.objects.filter(postazione_cq__isnull=True, postazione_assegnata__isnull=False):
        pcq = mapping.get(item.postazione_assegnata_id) or fallback
        if pcq:
            item.postazione_cq = pcq
            item.save(update_fields=['postazione_cq'])
            updated += 1

    # Anche items senza postazione_assegnata: assegna fallback
    for item in ItemOrdine.objects.filter(postazione_cq__isnull=True, postazione_assegnata__isnull=True):
        if fallback and item.servizio_prodotto.tipo == 'servizio':
            item.postazione_cq = fallback
            item.save(update_fields=['postazione_cq'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ordini', '0006_add_postazione_cq'),
        ('cq', '0007_prerequisiti_turni'),
    ]

    operations = [
        migrations.RunPython(popola_postazione_cq, noop),
    ]
