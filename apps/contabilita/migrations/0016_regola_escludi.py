"""Le regole dei movimenti possono escludere un testo ("Nexi, ma non se
contiene 'con carta'"). La regola Nexi prendeva anche gli acquisti fatti
con la carta Nexi aziendale (Amazon, PayPal, negozi...) e li metteva tra
le commissioni POS: d'ora in poi li salta, e quelli gia' classificati da
lei tornano da classificare. Quelli cambiati a mano restano."""
from django.db import migrations, models
from django.db.models import Q

ESCLUDI = 'con carta'


def avanti(apps, schema_editor):
    Regola = apps.get_model('contabilita', 'RegolaMovimento')
    Movimento = apps.get_model('contabilita', 'MovimentoFinanziario')
    for regola in Regola.objects.filter(testo__iexact='nexi', voce__codice='CV.COMMISSIONI', escludi=''):
        regola.escludi = ESCLUDI
        regola.save(update_fields=['escludi'])
        (Movimento.objects.filter(regola=regola)
         .filter(Q(descrizione__icontains=ESCLUDI) | Q(causale__icontains=ESCLUDI))
         .update(voce=None, regola=None, ripartizione={}))


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0015_voce_ticket_resto'),
    ]

    operations = [
        migrations.AddField(
            model_name='regolamovimento',
            name='escludi',
            field=models.CharField(blank=True, help_text='Non vale se la causale o la descrizione contiene anche questo testo',
                                   max_length=100),
        ),
        migrations.RunPython(avanti, migrations.RunPython.noop),
    ]
