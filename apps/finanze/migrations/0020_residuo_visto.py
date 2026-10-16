from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('finanze', '0019_abbinamento_scartato'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResiduoVisto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('visto_il', models.DateTimeField(auto_now_add=True)),
                ('operatore', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                related_name='residui_visti', to=settings.AUTH_USER_MODEL)),
                ('transazione', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                     related_name='residuo_visto', to='finanze.transazioneportale')),
            ],
            options={
                'verbose_name': 'Residuo visto',
                'verbose_name_plural': 'Residui visti',
            },
        ),
    ]
