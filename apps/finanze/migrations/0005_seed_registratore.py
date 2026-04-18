from django.db import migrations


def seed_registratore(apps, schema_editor):
    Cassa = apps.get_model('finanze', 'Cassa')
    Cassa.objects.get_or_create(
        nome='Registratore Servito',
        defaults={
            'numero': '',
            'tipo': 'automatica',  # appare nella lista casse da chiudere giornalmente
            'tracking_washcycles': False,
            'modalita_registratore': True,
            'attiva': True,
            'ordine': 0,  # appare per primo
        },
    )


def reverse_seed(apps, schema_editor):
    Cassa = apps.get_model('finanze', 'Cassa')
    Cassa.objects.filter(nome='Registratore Servito').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('finanze', '0004_modalita_registratore'),
    ]

    operations = [
        migrations.RunPython(seed_registratore, reverse_seed),
    ]
