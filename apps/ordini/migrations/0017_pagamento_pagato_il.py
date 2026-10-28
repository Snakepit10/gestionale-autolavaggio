from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('ordini', '0016_ordine_vendita_prodotti'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pagamento',
            name='data_pagamento',
            field=models.DateTimeField(default=django.utils.timezone.now, verbose_name='Pagato il'),
        ),
    ]
