from django.db import migrations, models


class Migration(migrations.Migration):
    """Il budget del servito passa dalle somme (lavaggi, ricavo, ore) alle
    leve per giorno. In produzione non c'erano budget salvati."""

    dependencies = [
        ('contabilita', '0018_budget_servito'),
    ]

    operations = [
        migrations.RemoveField(model_name='budgetservito', name='lavaggi'),
        migrations.RemoveField(model_name='budgetservito', name='ricavo_lavaggio'),
        migrations.RemoveField(model_name='budgetservito', name='ore_pagate'),
        migrations.AddField(
            model_name='budgetservito', name='giorni_aperti',
            field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(
            model_name='budgetservito', name='auto_giorno',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True)),
        migrations.AddField(
            model_name='budgetservito', name='prezzo_principale',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True,
                                      help_text='Ricavo per lavaggio senza gli extra, IVA esclusa')),
        migrations.AddField(
            model_name='budgetservito', name='pct_extra',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True)),
        migrations.AddField(
            model_name='budgetservito', name='valore_extra',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True,
                                      help_text='Ricavo medio di un lavaggio con extra dovuto agli extra, IVA esclusa')),
        migrations.AddField(
            model_name='budgetservito', name='persone_giorno',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
        migrations.AddField(
            model_name='budgetservito', name='ore_giornata',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True,
                                      help_text='Ore pagate per giorno-persona')),
    ]
