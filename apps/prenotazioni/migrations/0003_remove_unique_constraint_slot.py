# Generated manually for removing unique_together constraint

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('prenotazioni', '0002_prenotazione_tipo_auto'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='configurazioneslot',
            unique_together=set(),
        ),
    ]