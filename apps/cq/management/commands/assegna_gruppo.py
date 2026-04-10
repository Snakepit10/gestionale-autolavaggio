from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand, CommandError


GRUPPI_VALIDI = ['titolare', 'responsabile', 'operatore']


class Command(BaseCommand):
    help = 'Assegna un utente a un gruppo CQ (titolare, responsabile, operatore)'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='Username dell\'utente')
        parser.add_argument(
            '--gruppo',
            required=True,
            choices=GRUPPI_VALIDI,
            help=f'Gruppo: {", ".join(GRUPPI_VALIDI)}',
        )
        parser.add_argument(
            '--rimuovi',
            action='store_true',
            help='Rimuove l\'utente dal gruppo invece di aggiungerlo',
        )

    def handle(self, *args, **options):
        username = options['username']
        nome_gruppo = options['gruppo']
        rimuovi = options['rimuovi']

        try:
            utente = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'Utente non trovato: {username}')

        try:
            gruppo = Group.objects.get(name=nome_gruppo)
        except Group.DoesNotExist:
            raise CommandError(
                f'Gruppo "{nome_gruppo}" non trovato. Esegui prima: python manage.py crea_gruppi'
            )

        if rimuovi:
            utente.groups.remove(gruppo)
            self.stdout.write(
                self.style.WARNING(f'{username} rimosso dal gruppo "{nome_gruppo}"')
            )
        else:
            utente.groups.add(gruppo)
            self.stdout.write(
                self.style.SUCCESS(f'{username} aggiunto al gruppo "{nome_gruppo}"')
            )
