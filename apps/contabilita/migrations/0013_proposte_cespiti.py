import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0012_banca_estratto'),
    ]

    operations = [
        migrations.AddField(
            model_name='rigafatturapassiva',
            name='cespite',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='righe_fattura', to='contabilita.cespite'),
        ),
        migrations.AddField(
            model_name='rigafatturapassiva',
            name='non_cespite',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='movimentofinanziario',
            name='cespite',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='movimenti', to='contabilita.cespite'),
        ),
        migrations.AddField(
            model_name='movimentofinanziario',
            name='non_cespite',
            field=models.BooleanField(default=False),
        ),
    ]
