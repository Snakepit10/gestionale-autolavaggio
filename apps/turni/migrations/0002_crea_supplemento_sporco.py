from django.db import migrations


def crea_supplementi(apps, schema_editor):
    ServizioProdotto = apps.get_model('core', 'ServizioProdotto')
    Categoria = apps.get_model('core', 'Categoria')

    # Usa "Prelavaggio ed Extra" se esiste, altrimenti la prima categoria
    cat = Categoria.objects.filter(nome__icontains='extra').first()
    if not cat:
        cat = Categoria.objects.first()

    supplementi = [
        ('Supplemento sporco eccessivo', 10.00),
        ('Supplemento peli animali', 10.00),
    ]

    for titolo, prezzo in supplementi:
        ServizioProdotto.objects.get_or_create(
            titolo=titolo,
            defaults={
                'tipo': 'servizio',
                'prezzo': prezzo,
                'categoria': cat,
                'descrizione': f'{titolo} — aggiunto dall\'operatore durante la lavorazione',
                'durata_minuti': 0,
                'attivo': True,
                'is_supplemento': True,
            },
        )


def rimuovi_supplementi(apps, schema_editor):
    ServizioProdotto = apps.get_model('core', 'ServizioProdotto')
    ServizioProdotto.objects.filter(is_supplemento=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('turni', '0001_initial'),
        ('core', '0003_prerequisiti_turni'),
    ]

    operations = [
        migrations.RunPython(crea_supplementi, rimuovi_supplementi),
    ]
