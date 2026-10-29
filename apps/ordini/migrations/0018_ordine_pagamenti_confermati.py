from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ordini', '0017_pagamento_pagato_il'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordine',
            name='pagamenti_confermati',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
