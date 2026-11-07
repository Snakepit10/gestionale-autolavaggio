"""Tre voci per dividere gli incassi in contanti della cassa per fonte:
servito, casse automatiche e contanti senza scontrino. Si mettono subito
dopo la voce Incassi."""
from django.db import migrations

VOCI = [
    ('N.INC.SERVITO', 'Incassi in contanti dal servito'),
    ('N.INC.SELF', 'Incassi in contanti dalle casse automatiche'),
    ('N.INC.ALTRO', 'Incassi in contanti senza scontrino'),
]


def avanti(apps, schema_editor):
    VoceConto = apps.get_model('contabilita', 'VoceConto')
    incassi = VoceConto.objects.filter(codice='N.INCASSI').first()
    base = incassi.ordine if incassi else 0
    for i, (codice, nome) in enumerate(VOCI, 1):
        VoceConto.objects.get_or_create(codice=codice, defaults={
            'nome': nome, 'sezione': 'non_economico', 'flusso': 'operativo', 'ordine': base + i})


def indietro(apps, schema_editor):
    apps.get_model('contabilita', 'VoceConto').objects.filter(codice__in=[v[0] for v in VOCI]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0013_proposte_cespiti'),
    ]

    operations = [
        migrations.RunPython(avanti, indietro),
    ]
