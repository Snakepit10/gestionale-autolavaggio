"""Riconciliazione acquisti PayPal rimasti a meta'.

Caso coperto: il cliente approva il pagamento su paypal.com ma chiude
il browser PRIMA di tornare sulla pagina di ritorno -> l'ordine e'
APPROVED ma mai catturato, l'acquisto resta 'creato' e le monete non
arrivano. Questo comando (da cron, es. ogni 15 minuti insieme agli
altri) ritrova gli acquisti PayPal 'creato' piu' vecchi di 15 minuti,
interroga PayPal e cattura+accredita quelli approvati; marca 'fallito'
quelli scaduti/negati con piu' di 3 giorni.

Uso:
    python manage.py monete_riconcilia
    python manage.py monete_riconcilia --dry-run
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.monete.models import AcquistoMonete
from apps.monete.services import paypal_pay


class Command(BaseCommand):
    help = 'Cattura gli ordini PayPal approvati ma mai catturati (ritorno mancato).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Mostra cosa verrebbe fatto senza catturare.')

    def handle(self, *args, **options):
        if not paypal_pay.paypal_configurato():
            self.stdout.write('PayPal non configurato: skip.')
            return

        dry = options['dry_run']
        soglia = timezone.now() - timedelta(minutes=15)
        pendenti = AcquistoMonete.objects.filter(
            provider='paypal', stato='creato',
            creato_il__lt=soglia,
        ).exclude(provider_ref='')

        if not pendenti:
            self.stdout.write('Nessun acquisto PayPal da riconciliare.')
            return

        for acquisto in pendenti:
            stato = paypal_pay.stato_ordine(acquisto.provider_ref)
            if stato == 'APPROVED':
                if dry:
                    self.stdout.write(
                        f'[dry-run] acquisto {acquisto.pk}: APPROVED, catturerei.')
                    continue
                ok, msg, _ = paypal_pay.cattura_e_accredita(acquisto.provider_ref)
                stile = self.style.SUCCESS if ok else self.style.WARNING
                self.stdout.write(stile(f'Acquisto {acquisto.pk}: {msg}'))
            elif stato == 'COMPLETED':
                # Catturato ma non accreditato (crash a meta'): accredita
                if not dry:
                    from apps.monete.services.acquisti import accredita_acquisto
                    ok, msg = accredita_acquisto(acquisto.pk)
                    self.stdout.write(self.style.SUCCESS(
                        f'Acquisto {acquisto.pk} (gia\' catturato): {msg}'))
            elif acquisto.creato_il < timezone.now() - timedelta(days=3):
                # Mai approvato e ormai vecchio: chiudi la pratica
                if not dry:
                    acquisto.stato = 'fallito'
                    acquisto.save(update_fields=['stato', 'aggiornato_il'])
                self.stdout.write(
                    f'Acquisto {acquisto.pk}: stato PayPal "{stato or "?"}", '
                    f'marcato fallito (vecchio di 3+ giorni).')
            else:
                self.stdout.write(
                    f'Acquisto {acquisto.pk}: stato PayPal "{stato or "?"}", '
                    f'lascio in attesa.')
