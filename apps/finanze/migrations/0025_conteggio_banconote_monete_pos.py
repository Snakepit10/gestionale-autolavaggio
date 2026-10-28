from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def contanti_in_banconote(apps, schema_editor):
    # I conteggi gia' salvati hanno solo il totale: va tutto nelle banconote
    Q = apps.get_model('finanze', 'QuadraturaGiornaliera')
    for q in Q.objects.all():
        Q.objects.filter(pk=q.pk).update(contanti_banconote=q.contanti_totali)


class Migration(migrations.Migration):

    dependencies = [
        ('finanze', '0024_esecuzione_import_washtec'),
    ]

    operations = [
        migrations.AddField(
            model_name='quadraturagiornaliera',
            name='contanti_banconote',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10,
                                      validators=[django.core.validators.MinValueValidator(Decimal('0.00'))],
                                      verbose_name='Banconote'),
        ),
        migrations.AddField(
            model_name='quadraturagiornaliera',
            name='contanti_monete',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10,
                                      validators=[django.core.validators.MinValueValidator(Decimal('0.00'))],
                                      verbose_name='Monete'),
        ),
        migrations.AddField(
            model_name='quadraturagiornaliera',
            name='pos_casse_automatiche',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'),
                                      help_text='Pagamenti con carta fatti alle casse automatiche', max_digits=10,
                                      validators=[django.core.validators.MinValueValidator(Decimal('0.00'))],
                                      verbose_name='POS casse automatiche'),
        ),
        migrations.AlterField(
            model_name='quadraturagiornaliera',
            name='contanti_totali',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'),
                                      help_text='Banconote + monete (calcolato al salvataggio)', max_digits=10,
                                      validators=[django.core.validators.MinValueValidator(Decimal('0.00'))],
                                      verbose_name='Contanti totali conteggiati'),
        ),
        migrations.RunPython(contanti_in_banconote, migrations.RunPython.noop),
    ]
