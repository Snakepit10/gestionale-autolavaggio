"""Gli articoli dei prodotti di categorie "senza magazzino" (ricariche
credito) escono dal magazzino, con i loro movimenti (articoli non
tracciati: nessuna quantita' si perde)."""
from django.db import migrations


def avanti(apps, schema_editor):
    Articolo = apps.get_model('magazzino', 'Articolo')
    # (se per errore fossero stati assegnati o ordinati restano)
    (Articolo.objects.filter(prodotto__categoria__senza_magazzino=True)
     .filter(assegnazioni__isnull=True, righe_ordine__isnull=True, righe_consegna__isnull=True)
     .delete())


class Migration(migrations.Migration):
    dependencies = [
        ('magazzino', '0004_foto_articolo'),
        ('core', '0014_categoria_senza_magazzino'),
    ]
    operations = [migrations.RunPython(avanti, migrations.RunPython.noop)]
