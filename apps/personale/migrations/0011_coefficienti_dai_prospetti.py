"""Coefficienti lordi di default dai prospetti contabili di ottobre 2026,
con TFR (lordo / 13,5) e INAIL (circa 2.150 euro l'anno) compresi:
ordinari 1,76, apprendisti 1,30. Per gli apprendisti l'INPS a carico
dell'azienda e' quasi zero. Luca fino a maggio era tirocinante: senza
contributi ne' TFR, coefficiente 1. Da gennaio a ottobre ogni mese ha
anche il suo coefficiente esatto, impostato nella pagina del mese."""
from datetime import date
from decimal import Decimal

from django.db import migrations

APPRENDISTI = ('calogero', 'samuele', 'kimberly')


def avanti(apps, schema_editor):
    Parametro = apps.get_model('personale', 'ParametroRetributivo')
    for p in Parametro.objects.filter(dipendente__tipo='contrattualizzato').select_related('dipendente'):
        nome = p.dipendente.nome.strip().lower()
        if nome == 'luca' and p.valido_dal < date(2026, 6, 1):
            p.coefficiente_lordo, p.note = Decimal('1'), "Tirocinio: niente contributi ne' TFR"
        elif nome in APPRENDISTI:
            p.coefficiente_lordo, p.note = Decimal('1.30'), 'Dai prospetti 10/2026, TFR e INAIL compresi: apprendista'
        else:
            p.coefficiente_lordo, p.note = Decimal('1.76'), 'Dai prospetti 10/2026, TFR e INAIL compresi: ordinario'
        p.save(update_fields=['coefficiente_lordo', 'note'])


class Migration(migrations.Migration):

    dependencies = [
        ('personale', '0010_coefficienti_apprendisti_ordinari'),
    ]

    operations = [
        migrations.RunPython(avanti, migrations.RunPython.noop),
    ]
