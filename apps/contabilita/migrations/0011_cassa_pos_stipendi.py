from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0010_finanziamenti'),
    ]

    operations = [
        migrations.AddField(
            model_name='contofinanziario',
            name='pos_nei_contanti_fino_al',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contofinanziario',
            name='stipendi_in_contanti',
            field=models.BooleanField(default=False),
        ),
    ]
