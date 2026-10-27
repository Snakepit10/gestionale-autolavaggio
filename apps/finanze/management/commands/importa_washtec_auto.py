"""Import automatico delle transazioni WashTec Plus (Playwright).

Uso:
    python manage.py importa_washtec_auto              # un giro
    python manage.py importa_washtec_auto --ciclo      # worker Railway
    python manage.py importa_washtec_auto --dal 2026-10-01 --al 2026-10-05
    python manage.py importa_washtec_auto --dry-run --visibile   # prova in locale

Credenziali: WASHTEC_COMMITTENTE, WASHTEC_UTENTE e WASHTEC_PASSWORD. Orari del ciclo:
WASHTEC_ORA_INIZIO / WASHTEC_ORA_FINE (ora locale, default 7-22),
WASHTEC_INTERVALLO_MIN (default 10). Vedi docs/WASHTEC_IMPORT.md.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.finanze.services import import_automatico


def _data(testo):
    try:
        return datetime.strptime(testo, '%Y-%m-%d').date()
    except ValueError:
        raise CommandError(f'Data non valida: {testo} (usa AAAA-MM-GG)')


class Command(BaseCommand):
    help = 'Importa le transazioni WashTec Plus con un browser headless.'

    def add_arguments(self, parser):
        parser.add_argument('--ciclo', action='store_true',
                            help='Worker: giri programmati e richieste "Aggiorna ora".')
        parser.add_argument('--dal', type=_data, help='Primo giorno (AAAA-MM-GG).')
        parser.add_argument('--al', type=_data, help='Ultimo giorno (AAAA-MM-GG).')
        parser.add_argument('--dry-run', action='store_true', help='Legge da WashTec senza salvare.')
        parser.add_argument('--visibile', action='store_true', help='Mostra il browser (debug in locale).')

    def handle(self, *args, **opz):
        problema = import_automatico.verifica_database()
        if problema:
            raise CommandError(problema)
        if opz['ciclo']:
            self.stdout.write('Import WashTec: worker avviato.')
            import_automatico.ciclo(scrivi=self.stdout.write)
            return
        estrai = None
        if opz['visibile']:
            estrai = lambda dal, al: import_automatico.estrai_playwright(dal, al, visibile=True)
        e = import_automatico.esegui(estrai=estrai, dal=opz['dal'], al=opz['al'],
                                     conferma=not opz['dry_run'])
        if opz['dry_run']:
            # una prova non e' un aggiornamento: non deve comparire nello stato
            e.delete()
        if e.esito == 'errore':
            raise CommandError(f'Import WashTec non riuscito: {e.messaggio}')
        cosa = 'da importare (dry-run, nulla salvato)' if opz['dry_run'] else 'importate'
        self.stdout.write(self.style.SUCCESS(
            f'{e.dal:%d/%m}-{e.al:%d/%m}: {e.righe} righe lette, {e.nuove} nuove {cosa}.'))
        if e.messaggio:
            self.stdout.write(self.style.WARNING(f'Da verificare:\n{e.messaggio}'))
