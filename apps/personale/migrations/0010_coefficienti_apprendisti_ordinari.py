"""Coefficiente lordo diviso fra apprendisti e ordinari, sempre dai
cedolini di settembre 2026 (TFR compreso, INAIL escluso):
- apprendisti 1,30: contributi a carico dell'azienda molto bassi;
- ordinari 1,71.
Il totale resta quello della media 1,52 (circa 11.670 su 7.694 di netto in
busta). Gli apprendisti sono Calogero, Samuele e Kimberly. Quando un
apprendista passa al secondo anno o diventa ordinario il coefficiente va
aggiornato da Personale > Dipendenti."""
from decimal import Decimal

from django.db import migrations

APPRENDISTI = ('calogero', 'samuele', 'kimberly')
VECCHIA_NOTA = 'Coefficiente medio dai cedolini 09/2026, TFR compreso'


def avanti(apps, schema_editor):
    Parametro = apps.get_model('personale', 'ParametroRetributivo')
    for p in Parametro.objects.filter(dipendente__tipo='contrattualizzato').select_related('dipendente'):
        apprendista = p.dipendente.nome.strip().lower() in APPRENDISTI
        p.coefficiente_lordo = Decimal('1.30') if apprendista else Decimal('1.71')
        if not p.note or p.note == VECCHIA_NOTA:
            p.note = f'Dai cedolini 09/2026, TFR compreso: {"apprendista" if apprendista else "ordinario"}'
        p.save(update_fields=['coefficiente_lordo', 'note'])


class Migration(migrations.Migration):

    dependencies = [
        ('personale', '0009_coefficiente_dai_cedolini'),
    ]

    operations = [
        migrations.RunPython(avanti, migrations.RunPython.noop),
    ]
