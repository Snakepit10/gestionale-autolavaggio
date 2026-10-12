"""Le assegnazioni passano dalle postazioni ai posti.

Ogni posto puo' essere collegato a una postazione del turno (checklist e
dotazione degli operatori). Le assegnazioni gia' fatte a una postazione
vanno al posto collegato a quella postazione: se non c'e' si usa il posto
con lo stesso nome (e lo si collega) o se ne crea uno.
"""
import django.db.models.deletion
from django.db import migrations, models


def postazioni_a_posti(apps, schema_editor):
    Assegnazione = apps.get_model('magazzino', 'Assegnazione')
    Posto = apps.get_model('magazzino', 'Posto')
    per_postazione = {}
    for a in Assegnazione.objects.filter(postazione__isnull=False).select_related('postazione'):
        postazione = a.postazione
        posto = per_postazione.get(postazione.pk)
        if posto is None:
            posto = Posto.objects.filter(postazione=postazione).first()
        if posto is None:
            posto = Posto.objects.filter(nome__iexact=postazione.nome, postazione__isnull=True).first()
            if posto:
                posto.postazione = postazione
                posto.save(update_fields=['postazione'])
        if posto is None:
            nome = postazione.nome
            if Posto.objects.filter(nome__iexact=nome).exists():
                nome = f'{nome} (postazione)'
            posto = Posto.objects.create(nome=nome[:80], postazione=postazione)
        per_postazione[postazione.pk] = posto
        a.posto = posto
        a.save(update_fields=['posto'])


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0010_esito_3_livelli'),
        ('magazzino', '0009_giacenze_per_posto'),
    ]

    operations = [
        migrations.AddField(
            model_name='posto',
            name='postazione',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='posti_magazzino', to='cq.postazionecq',
                                    verbose_name='Postazione del turno'),
        ),
        migrations.AddField(
            model_name='assegnazione',
            name='posto',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='assegnazioni', to='magazzino.posto'),
        ),
        migrations.RunPython(postazioni_a_posti, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='assegnazione',
            name='postazione',
        ),
    ]
