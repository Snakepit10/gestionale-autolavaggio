from django.core.management.base import BaseCommand
from apps.clienti.models import Cliente


class Command(BaseCommand):
    help = 'Converte le email vuote in NULL per evitare conflitti UNIQUE'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra i cambiamenti senza applicarli',
        )

    def handle(self, *args, **options):
        # Trova clienti con email vuota (stringa vuota)
        clienti_con_email_vuota = Cliente.objects.filter(email='')
        
        self.stdout.write(f"Trovati {clienti_con_email_vuota.count()} clienti con email vuota...")
        
        if clienti_con_email_vuota.count() == 0:
            self.stdout.write(
                self.style.SUCCESS('Nessun cliente con email vuota trovato. Database già pulito.')
            )
            return
        
        if options['dry_run']:
            self.stdout.write("Dry-run: I seguenti clienti avrebbero l'email convertita da '' a NULL:")
            for cliente in clienti_con_email_vuota:
                self.stdout.write(f"  - {cliente.nome_completo} (ID: {cliente.id})")
        else:
            # Aggiorna le email vuote a NULL
            aggiornati = clienti_con_email_vuota.update(email=None)
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Aggiornati {aggiornati} clienti: email vuota convertita in NULL'
                )
            )