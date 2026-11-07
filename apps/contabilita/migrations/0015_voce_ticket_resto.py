"""Voce propria per i ticket di resto pagati dalla cassa, che prima
stavano sulla voce Incassi insieme agli accrediti POS. I movimenti dei
ticket ancora sulla voce Incassi passano alla nuova voce; quelli spostati
a mano su un'altra voce restano dove sono."""
from django.db import migrations


def avanti(apps, schema_editor):
    VoceConto = apps.get_model('contabilita', 'VoceConto')
    MovimentoFinanziario = apps.get_model('contabilita', 'MovimentoFinanziario')
    altro = VoceConto.objects.filter(codice='N.INC.ALTRO').first()
    voce, _ = VoceConto.objects.get_or_create(codice='N.TICKET', defaults={
        'nome': 'Ticket di resto pagati', 'sezione': 'non_economico', 'flusso': 'operativo',
        'ordine': (altro.ordine if altro else 0) + 1})
    MovimentoFinanziario.objects.filter(impronta__startswith='cassa|ticket|', voce__codice='N.INCASSI').update(voce=voce)


def indietro(apps, schema_editor):
    VoceConto = apps.get_model('contabilita', 'VoceConto')
    MovimentoFinanziario = apps.get_model('contabilita', 'MovimentoFinanziario')
    incassi = VoceConto.objects.filter(codice='N.INCASSI').first()
    MovimentoFinanziario.objects.filter(voce__codice='N.TICKET').update(voce=incassi)
    VoceConto.objects.filter(codice='N.TICKET').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0014_voci_incassi_contanti'),
    ]

    operations = [
        migrations.RunPython(avanti, indietro),
    ]
