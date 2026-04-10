from django.core.management.base import BaseCommand
from apps.clienti.models import Cliente


class Command(BaseCommand):
    help = 'Converte i nomi dei clienti esistenti in title case'

    def handle(self, *args, **options):
        clienti = Cliente.objects.all()
        updated_count = 0
        
        for cliente in clienti:
            changed = False
            
            # Aggiorna nome se presente
            if cliente.nome and cliente.nome != cliente.nome.title():
                cliente.nome = cliente.nome.title()
                changed = True
            
            # Aggiorna cognome se presente
            if cliente.cognome and cliente.cognome != cliente.cognome.title():
                cliente.cognome = cliente.cognome.title()
                changed = True
            
            # Aggiorna ragione sociale se presente
            if cliente.ragione_sociale and cliente.ragione_sociale != cliente.ragione_sociale.title():
                cliente.ragione_sociale = cliente.ragione_sociale.title()
                changed = True
            
            # Aggiorna città se presente
            if cliente.citta and cliente.citta != cliente.citta.title():
                cliente.citta = cliente.citta.title()
                changed = True
            
            # Aggiorna indirizzo se presente
            if cliente.indirizzo and cliente.indirizzo != cliente.indirizzo.title():
                cliente.indirizzo = cliente.indirizzo.title()
                changed = True
            
            if changed:
                cliente.save()
                updated_count += 1
                self.stdout.write(f'Aggiornato: {cliente}')
        
        self.stdout.write(
            self.style.SUCCESS(f'Aggiornati {updated_count} clienti su {clienti.count()} totali')
        )