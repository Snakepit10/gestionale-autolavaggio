"""Permessi iniziali = quello che ogni utente vede e usa oggi.

- Le voci del menu senza condizione (Clienti, Cassa, Task, Ordini,
  Fatture, Abbonamenti, Prenotazioni, Qualita') vanno ai tre gruppi.
- Il Mio Turno: operatore. Report turni: titolare e responsabile.
- Finanze, Configurazione, Monete: titolare. Il titolare ha tutto.
- Messaggi, Marketing e Monete oggi funzionano per gli utenti staff
  (is_staff): eccezione "consenti" per gli staff non superuser; quelli
  senza gruppo ricevono anche le voci del menu visibili a tutti.
"""
from django.db import migrations

BASE = ['clienti', 'cassa', 'task', 'ordini', 'fatture', 'abbonamenti', 'prenotazioni', 'qualita']
PER_GRUPPO = {
    'operatore': BASE + ['mio_turno'],
    'responsabile': BASE + ['report_turni'],
    'titolare': BASE + ['messaggi', 'marketing', 'mio_turno', 'report_turni', 'finanze',
                        'monete', 'configurazione'],
}
SOLO_STAFF = ['messaggi', 'marketing', 'monete']


def avanti(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    PermessoGruppo = apps.get_model('auth_system', 'PermessoGruppo')
    PermessoUtente = apps.get_model('auth_system', 'PermessoUtente')

    for nome, sezioni in PER_GRUPPO.items():
        gruppo, _ = Group.objects.get_or_create(name=nome)
        for sezione in sezioni:
            PermessoGruppo.objects.get_or_create(sezione=sezione, gruppo=gruppo)

    for user in User.objects.filter(is_staff=True, is_superuser=False):
        sezioni = list(SOLO_STAFF)
        if not user.groups.exists():
            sezioni += BASE
        for sezione in sezioni:
            PermessoUtente.objects.get_or_create(sezione=sezione, user=user,
                                                 defaults={'consenti': True})


def indietro(apps, schema_editor):
    apps.get_model('auth_system', 'PermessoGruppo').objects.all().delete()
    apps.get_model('auth_system', 'PermessoUtente').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('auth_system', '0001_permessi_sezioni'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(avanti, indietro)]
