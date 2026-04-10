"""
Comando di management per aggiornare le durate stimate di tutti gli ordini esistenti.

Uso:
    python manage.py aggiorna_durate_ordini

Questo comando calcola la durata stimata per tutti gli ordini che hanno durata_stimata_minuti = 0,
basandosi sui servizi contenuti nell'ordine.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count
from apps.ordini.models import Ordine


class Command(BaseCommand):
    help = 'Aggiorna le durate stimate di tutti gli ordini esistenti'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Forza il ricalcolo anche per ordini che hanno già una durata',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simula l\'operazione senza salvare modifiche',
        )

    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']

        self.stdout.write(self.style.MIGRATE_HEADING('Aggiornamento durate ordini'))
        self.stdout.write('')

        # Filtra ordini
        if force:
            ordini = Ordine.objects.filter(stato__in=['in_attesa', 'in_lavorazione'])
            self.stdout.write(f'Modalità FORCE: aggiorno tutti gli ordini attivi')
        else:
            ordini = Ordine.objects.filter(
                stato__in=['in_attesa', 'in_lavorazione'],
                durata_stimata_minuti=0
            )
            self.stdout.write(f'Modalità normale: aggiorno solo ordini con durata = 0')

        ordini = ordini.prefetch_related('items__servizio_prodotto')
        totale_ordini = ordini.count()

        if totale_ordini == 0:
            self.stdout.write(self.style.SUCCESS('✓ Nessun ordine da aggiornare'))
            return

        self.stdout.write(f'Trovati {totale_ordini} ordini da aggiornare')
        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING('MODALITÀ DRY-RUN: nessuna modifica verrà salvata'))
            self.stdout.write('')

        aggiornati = 0
        errori = 0

        for ordine in ordini:
            try:
                durata_vecchia = ordine.durata_stimata_minuti
                durata_calcolata = ordine.calcola_durata_da_servizi()

                if durata_vecchia != durata_calcolata or force:
                    if not dry_run:
                        if force:
                            ordine.aggiorna_durata_stimata(forza_ricalcolo=True)
                        else:
                            ordine.aggiorna_durata_stimata()

                    aggiornati += 1

                    # Log dettagliato
                    if durata_vecchia == 0:
                        self.stdout.write(
                            f'  #{ordine.numero_breve}: '
                            f'{self.style.SUCCESS("✓")} '
                            f'durata calcolata: {durata_calcolata} minuti'
                        )
                    else:
                        self.stdout.write(
                            f'  #{ordine.numero_breve}: '
                            f'{self.style.WARNING("↻")} '
                            f'{durata_vecchia} min → {durata_calcolata} min'
                        )

            except Exception as e:
                errori += 1
                self.stdout.write(
                    f'  #{ordine.numero_breve}: '
                    f'{self.style.ERROR("✗")} '
                    f'Errore: {str(e)}'
                )

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Riepilogo'))
        self.stdout.write(f'  Ordini processati: {totale_ordini}')
        self.stdout.write(f'  Ordini aggiornati: {aggiornati}')

        if errori > 0:
            self.stdout.write(self.style.ERROR(f'  Errori: {errori}'))
        else:
            self.stdout.write(self.style.SUCCESS('  Errori: 0'))

        if dry_run:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'ATTENZIONE: Modalità dry-run - nessuna modifica è stata salvata'
            ))
            self.stdout.write('Esegui senza --dry-run per applicare le modifiche')
        else:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('✓ Operazione completata con successo'))
