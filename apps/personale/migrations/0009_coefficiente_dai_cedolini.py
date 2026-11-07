"""Coefficiente lordo 1,52 per tutti i dipendenti contrattualizzati, su
tutti i loro parametri: costo aziendale / netto in busta dei cedolini di
settembre 2026 (lordo 9.378 + contributi a carico dell'azienda 1.614 + TFR
maturato circa 680, su 7.694 di netto in busta). Comprende il TFR; non
l'INAIL, che si paga una volta l'anno. E' una media: gli apprendisti
costano meno, gli altri di piu'. Si corregge da Personale > Dipendenti con
i valori del consulente paghe."""
from decimal import Decimal

from django.db import migrations

COEFFICIENTE = Decimal('1.52')
NOTA = 'Coefficiente medio dai cedolini 09/2026, TFR compreso'


def avanti(apps, schema_editor):
    Parametro = apps.get_model('personale', 'ParametroRetributivo')
    for p in Parametro.objects.filter(dipendente__tipo='contrattualizzato'):
        p.coefficiente_lordo = COEFFICIENTE
        if not p.note:
            p.note = NOTA
        p.save(update_fields=['coefficiente_lordo', 'note'])


class Migration(migrations.Migration):

    dependencies = [
        ('personale', '0008_cicli_operatori'),
    ]

    operations = [
        migrations.RunPython(avanti, migrations.RunPython.noop),
    ]
