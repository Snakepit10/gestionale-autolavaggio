from django.core.management.base import BaseCommand
from apps.ordini.models import ItemOrdine


class Command(BaseCommand):
    help = 'Ricalcola le assegnazioni delle postazioni per gli ordini in attesa/lavorazione'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra i cambiamenti senza applicarli',
        )
        parser.add_argument(
            '--servizio-id',
            type=int,
            help='Ricalcola solo per un servizio specifico (ID)',
        )

    def handle(self, *args, **options):
        # Trova gli item in attesa o in lavorazione
        queryset = ItemOrdine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione'],
            servizio_prodotto__tipo='servizio'
        )
        
        if options['servizio_id']:
            queryset = queryset.filter(servizio_prodotto_id=options['servizio_id'])
        
        self.stdout.write(f"Trovati {queryset.count()} item da verificare...")
        
        cambiamenti = 0
        for item in queryset:
            postazioni_configurate = item.servizio_prodotto.postazioni.filter(attiva=True)
            
            if not postazioni_configurate.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"ATTENZIONE: Servizio '{item.servizio_prodotto.titolo}' "
                        f"non ha postazioni configurate!"
                    )
                )
                continue
            
            # Verifica se l'assegnazione attuale è ancora valida
            if item.postazione_assegnata not in postazioni_configurate:
                self.stdout.write(
                    f"Item {item.id} ({item.servizio_prodotto.titolo}): "
                    f"{item.postazione_assegnata} -> "
                    f"richiede ricalcolo"
                )
                
                if not options['dry_run']:
                    risultato = item.ricalcola_postazione()
                    self.stdout.write(f"  {risultato}")
                    if "spostato" in risultato:
                        cambiamenti += 1
                else:
                    # Calcola quale sarebbe la nuova postazione
                    nuova_postazione = min(
                        postazioni_configurate,
                        key=lambda p: p.get_ordini_in_coda().count()
                    )
                    self.stdout.write(f"  Sarebbe spostato a: {nuova_postazione}")
                    cambiamenti += 1
        
        if options['dry_run']:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry-run completato. {cambiamenti} item richiederebbero cambiamenti."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Ricalcolo completato. {cambiamenti} item aggiornati."
                )
            )