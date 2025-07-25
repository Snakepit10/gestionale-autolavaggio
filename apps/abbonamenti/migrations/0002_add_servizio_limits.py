# Generated manually for new service limits

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('abbonamenti', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='servizioinclusoabbonamento',
            name='accessi_totali_periodo',
            field=models.IntegerField(
                blank=True,
                help_text='Limite totale accessi nell\'intero periodo dell\'abbonamento',
                null=True
            ),
        ),
        migrations.AddField(
            model_name='servizioinclusoabbonamento',
            name='accessi_per_sottoperiodo',
            field=models.IntegerField(
                blank=True,
                help_text='Limite accessi per sotto-periodo (es. settimanale se reset è mensile)',
                null=True
            ),
        ),
        migrations.AddField(
            model_name='servizioinclusoabbonamento',
            name='tipo_sottoperiodo',
            field=models.CharField(
                blank=True,
                choices=[
                    ('giornaliero', 'Giornaliero'),
                    ('settimanale', 'Settimanale'),
                    ('mensile', 'Mensile')
                ],
                help_text='Tipo di sotto-periodo per il limite aggiuntivo',
                max_length=20,
                null=True
            ),
        ),
        migrations.AlterField(
            model_name='servizioinclusoabbonamento',
            name='quantita_inclusa',
            field=models.IntegerField(help_text='Numero accessi per periodo di reset'),
        ),
    ]