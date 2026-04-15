from django.db import migrations


def popola_sigle(apps, schema_editor):
    PostazioneCQ = apps.get_model('cq', 'PostazioneCQ')
    sigle = {
        'post1': 'PL',
        'post2': 'SP',
        'post3': 'AS',
        'post4': 'PV',
        'controllo_finale': 'CF',
    }
    for codice, sigla in sigle.items():
        PostazioneCQ.objects.filter(codice=codice).update(sigla=sigla)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0008_add_sigla_postazione'),
    ]

    operations = [
        migrations.RunPython(popola_sigle, noop),
    ]
