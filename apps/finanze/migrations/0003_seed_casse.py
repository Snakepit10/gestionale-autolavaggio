from django.db import migrations


CASSE_SEED = [
    # (nome, numero, tipo, tracking_washcycles, ordine)
    ('Cassa Servito',     '',       'servito',    False, 1),
    ('Cambia Gettoni',    '11064',  'automatica', False, 2),
    ('Portale Blu',       '11057',  'automatica', True,  3),
    ('Portale Azzurro',   '11061',  'automatica', True,  4),
]


def seed_casse(apps, schema_editor):
    Cassa = apps.get_model('finanze', 'Cassa')
    for nome, numero, tipo, wc, ordine in CASSE_SEED:
        Cassa.objects.get_or_create(
            nome=nome,
            defaults={
                'numero': numero,
                'tipo': tipo,
                'tracking_washcycles': wc,
                'attiva': True,
                'ordine': ordine,
            },
        )


def reverse_seed(apps, schema_editor):
    Cassa = apps.get_model('finanze', 'Cassa')
    Cassa.objects.filter(nome__in=[n for n, *_ in CASSE_SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('finanze', '0002_casse_multi'),
    ]

    operations = [
        migrations.RunPython(seed_casse, reverse_seed),
    ]
