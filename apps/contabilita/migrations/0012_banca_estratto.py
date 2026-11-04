from django.db import migrations, models


def avanti(apps, schema_editor):
    Conto = apps.get_model('contabilita', 'ContoFinanziario')
    for nome in ('BAPS', 'MPS'):
        Conto.objects.filter(nome=nome).update(banca_estratto=nome)


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0011_cassa_pos_stipendi'),
    ]

    operations = [
        migrations.AddField(
            model_name='contofinanziario',
            name='banca_estratto',
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.RunPython(avanti, migrations.RunPython.noop),
    ]
