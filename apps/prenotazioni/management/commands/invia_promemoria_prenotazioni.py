"""Invia il promemoria WhatsApp pre-appuntamento per le prenotazioni
confermate che iniziano nella prossima ora circa.

Pensato per essere eseguito da un Railway Cron service ogni 15 min:

    python manage.py invia_promemoria_prenotazioni

Idempotente: il flag `Prenotazione.promemoria_inviato` previene
duplicati. Se l'invio WhatsApp fallisce il flag NON viene settato, in
modo che la prossima esecuzione ritenti (ma cade comunque sul fallback
email lato `notifica_prenotazione_promemoria`).

Finestra temporale: cerca slot tra `now + min_anticipo` e
`now + max_anticipo` (default 45/90 min). Con cron ogni 15 min e
finestra 45-90 ogni prenotazione viene catturata in ~3 esecuzioni
successive ma inviata una sola volta (flag idempotente).
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clients.notifications import notifica_prenotazione_promemoria
from apps.prenotazioni.models import Prenotazione


class Command(BaseCommand):
    help = 'Invia promemoria WhatsApp per le prenotazioni che iniziano tra 45-90 min.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-anticipo',
            type=int,
            default=45,
            help='Minuti minimi di anticipo dalla partenza dello slot (default 45)',
        )
        parser.add_argument(
            '--max-anticipo',
            type=int,
            default=90,
            help='Minuti massimi di anticipo dalla partenza dello slot (default 90)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra solo cosa farebbe, senza inviare ne settare il flag',
        )

    def handle(self, *args, **opts):
        min_a = int(opts['min_anticipo'])
        max_a = int(opts['max_anticipo'])
        dry = bool(opts['dry_run'])

        now = timezone.localtime(timezone.now())
        finestra_inizio = now + timedelta(minutes=min_a)
        finestra_fine = now + timedelta(minutes=max_a)

        # Filtriamo prima per data (oggi o domani per la finestra a
        # cavallo di mezzanotte), poi in Python sulla data_ora effettiva.
        date_candidati = {finestra_inizio.date(), finestra_fine.date()}

        qs = (
            Prenotazione.objects
            .filter(
                stato='confermata',
                promemoria_inviato=False,
                slot__data__in=date_candidati,
            )
            .select_related('cliente', 'slot')
        )

        candidati = []
        for p in qs:
            slot_dt = timezone.make_aware(
                datetime.combine(p.slot.data, p.slot.ora_inizio)
            )
            if finestra_inizio <= slot_dt <= finestra_fine:
                candidati.append((p, slot_dt))

        if not candidati:
            self.stdout.write(self.style.SUCCESS(
                f'0 promemoria da inviare (finestra {min_a}-{max_a} min).'
            ))
            return

        inviati = 0
        falliti = 0
        for p, slot_dt in candidati:
            label = f'{p.codice_prenotazione} slot={slot_dt:%Y-%m-%d %H:%M}'
            if dry:
                self.stdout.write(f'[DRY] avrei inviato promemoria per {label}')
                continue
            try:
                ok = notifica_prenotazione_promemoria(p)
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f'Errore notifica per {label}: {e}'
                ))
                ok = False
            if ok:
                p.promemoria_inviato = True
                p.save(update_fields=['promemoria_inviato'])
                inviati += 1
                self.stdout.write(self.style.SUCCESS(
                    f'OK promemoria {label}'
                ))
            else:
                falliti += 1
                self.stdout.write(self.style.WARNING(
                    f'KO promemoria {label} (verra ritentato al prossimo giro)'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'Promemoria: {inviati} inviati, {falliti} falliti su {len(candidati)} candidati.'
        ))
