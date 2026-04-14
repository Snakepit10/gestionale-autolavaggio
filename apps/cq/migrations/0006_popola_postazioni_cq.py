from django.db import migrations


def popola_postazioni(apps, schema_editor):
    PostazioneCQ = apps.get_model('cq', 'PostazioneCQ')
    dati = [
        ('post1', 'Postazione 1 — Pre-lavaggio', 0, False),
        ('post2', 'Postazione 2 — Spazzole', 1, False),
        ('post3', 'Postazione 3 — Aspirazione', 2, False),
        ('post4', 'Postazione 4 — Plastiche e vetri', 3, False),
        ('controllo_finale', 'Controllo finale', 4, True),
    ]
    for codice, nome, ordine, is_cf in dati:
        PostazioneCQ.objects.get_or_create(
            codice=codice,
            defaults={
                'nome': nome,
                'ordine': ordine,
                'attiva': True,
                'is_controllo_finale': is_cf,
            },
        )


def rimuovi_postazioni(apps, schema_editor):
    PostazioneCQ = apps.get_model('cq', 'PostazioneCQ')
    PostazioneCQ.objects.filter(
        codice__in=['post1', 'post2', 'post3', 'post4', 'controllo_finale']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0005_postazioni_blocchi_presets'),
    ]

    operations = [
        migrations.RunPython(popola_postazioni, rimuovi_postazioni),
    ]
