# Scambia contanti <-> unita' sulle transazioni portale gia' importate:
# le etichette del "Metodo di pagamento" di WashTec sono invertite
# rispetto alla realta' operativa (vedi nota su ORIGINE_CHOICES).
from django.db import migrations


def swap_origine(apps, schema_editor):
    TransazionePortale = apps.get_model('finanze', 'TransazionePortale')
    TransazionePortale.objects.filter(origine='contanti').update(origine='_swap')
    TransazionePortale.objects.filter(origine='unita').update(origine='contanti')
    TransazionePortale.objects.filter(origine='_swap').update(origine='unita')


class Migration(migrations.Migration):

    dependencies = [
        ('finanze', '0009_transazioneportale_chiusuraportali'),
    ]

    operations = [
        # Lo swap e' la propria inversa
        migrations.RunPython(swap_origine, swap_origine),
    ]
