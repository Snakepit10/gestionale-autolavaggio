from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('personale', '0004_senza_previsione_self'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametriprevisione',
            name='margine_pct',
            field=models.DecimalField(decimal_places=1, default=Decimal('10'), max_digits=4,
                                      help_text='Quanto aggiungere ai lavaggi previsti prima di calcolare la squadra'),
        ),
    ]
