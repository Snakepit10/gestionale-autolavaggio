"""Cron marketing: aggiorna i flussi continui + processa la coda invii.

Pensato per girare come Railway cron service ogni 15 minuti (stesso
pattern di invia_promemoria_prenotazioni). Ogni run:

1. FLUSSI CONTINUI: per ogni campagna con flusso_continuo attivo (e
   non in pausa/annullata) rivaluta i suoi segmenti e accoda i clienti
   che vi sono entrati; ritenta i saltati per motivi temporanei; se la
   campagna ha riaggancio_giorni > 0, rimette in coda i clienti gia'
   contattati che sono rientrati nel segmento dopo quel periodo.
   (Sostituisce il vecchio richiamo automatico hardcoded: oggi un
   "richiamo" e' una normale campagna a flusso continuo su un segmento
   tipo dormienti o un segmento personalizzato "fermi da 45+ giorni".)

2. CODA INVII: preleva gli InvioCampagna in_coda (campagne in_coda o
   in_corso, le piu' vecchie prima) rispettando:
   - fascia oraria e tetto giornaliero max_invii_giorno
   - massimo --max-batch invii per run (default 8)
   - pausa casuale tra intervallo_min e intervallo_max secondi
   Per ogni invio: ri-verifica eleggibilita', risolve i placeholder
   con dati freschi, invia e collega il MessaggioWhatsApp.

Uso:
    python manage.py esegui_campagne_marketing
    python manage.py esegui_campagne_marketing --dry-run
    python manage.py esegui_campagne_marketing --max-batch 5
"""
from django.core.management.base import BaseCommand

from apps.marketing.models import ImpostazioniMarketing
from apps.marketing.services.campagne import aggiorna_flussi_continui


class Command(BaseCommand):
    help = 'Aggiorna i flussi continui + processa la coda invii marketing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-batch', type=int, default=8,
            help='Massimo invii per singolo run (default 8).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Mostra cosa verrebbe fatto senza inviare nulla.',
        )

    def handle(self, *args, **options):
        cfg = ImpostazioniMarketing.get_solo()
        dry = options['dry_run']
        max_batch = options['max_batch']

        aggiorna_flussi_continui(log=self.stdout.write, dry=dry)
        self._processa_coda(cfg, dry, max_batch)

    def _processa_coda(self, cfg, dry, max_batch):
        # Logica in apps/marketing/services/invio.py: condivisa col
        # bottone "Processa coda ora" della UI. Il claim atomico
        # per-riga rende sicura l'esecuzione concorrente cron+web.
        from apps.marketing.services.invio import processa_coda
        processa_coda(max_batch=max_batch, log=self.stdout.write, dry=dry)
