from django.db import migrations


def crea_gruppi(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for nome in ['titolare', 'responsabile', 'operatore']:
        Group.objects.get_or_create(name=nome)


def rimuovi_gruppi(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['titolare', 'responsabile', 'operatore']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(crea_gruppi, rimuovi_gruppi),
    ]
