"""Legge le fatture dei fornitori dalla casella PEC.

Gira nel servizio cron di Railway (ogni 15 minuti, vedi docs/CONTABILITA.md):
    python manage.py leggi_pec_fatture
"""
from django.core.management.base import BaseCommand

from apps.contabilita.pec import ErrorePec, configurata, leggi_casella


class Command(BaseCommand):
    help = 'Importa le fatture dei fornitori arrivate sulla PEC'

    def add_arguments(self, parser):
        parser.add_argument('--giorni', type=int, default=30,
                            help='quanti giorni indietro guardare (default 30)')

    def handle(self, *args, giorni, **options):
        if not configurata():
            self.stdout.write('PEC non configurata: niente da leggere.')
            return
        try:
            letti, esito = leggi_casella(giorni)
        except ErrorePec as e:
            self.stderr.write(f'Errore: {e}')
            return
        self.stdout.write(f'Messaggi letti: {letti}; fatture: {esito.testo()}')
        for errore in esito.errori:
            self.stderr.write(f'  {errore}')
