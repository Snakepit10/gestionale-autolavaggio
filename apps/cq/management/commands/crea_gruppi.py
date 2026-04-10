from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Crea i gruppi Django per il sistema CQ: titolare, responsabile, operatore'

    def handle(self, *args, **options):
        for nome in ['titolare', 'responsabile', 'operatore']:
            gruppo, creato = Group.objects.get_or_create(name=nome)
            if creato:
                self.stdout.write(self.style.SUCCESS(f'Gruppo creato: {nome}'))
            else:
                self.stdout.write(f'Gruppo già esistente: {nome}')
