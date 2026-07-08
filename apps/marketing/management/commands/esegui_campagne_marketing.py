"""Cron marketing: processa la coda invii + genera i richiami automatici.

Pensato per girare come Railway cron service ogni 15 minuti (stesso
pattern di invia_promemoria_prenotazioni). Ogni run:

1. RICHIAMO AUTOMATICO (se attivo nelle impostazioni): trova i clienti
   il cui ultimo lavaggio completato risale a `richiamo_giorni_dopo`
   giorni fa (finestra sul giorno esatto: il cron gira piu' volte al
   giorno ma il cliente viene accodato una sola volta grazie a
   unique_together e alla campagna mensile) e li accoda alla campagna
   'Richiamo automatico YYYY-MM'.

2. CODA INVII: preleva gli InvioCampagna in_coda (campagne in_coda o
   in_corso, le piu' vecchie prima) rispettando:
   - tetto giornaliero max_invii_giorno (contando gli inviati oggi
     su TUTTE le campagne)
   - massimo --max-batch invii per run (default 8: con pause di
     45-180s tra invii, 8 messaggi stanno in un run da 15 minuti)
   - pausa casuale tra intervallo_min e intervallo_max secondi
   Per ogni invio: ri-verifica eleggibilita' (l'opt-out puo' essere
   arrivato dopo il lancio), risolve i placeholder con dati freschi,
   chiama _send_template_blocking (che logga gia' in MessaggioWhatsApp)
   e collega il messaggio all'invio per lo stato di consegna.

Uso:
    python manage.py esegui_campagne_marketing
    python manage.py esegui_campagne_marketing --dry-run
    python manage.py esegui_campagne_marketing --max-batch 5
"""
import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from apps.clienti.models import Cliente
from apps.marketing.models import Campagna, ImpostazioniMarketing, InvioCampagna
from apps.marketing.services.campagne import risolvi_params, verifica_eleggibilita


class Command(BaseCommand):
    help = 'Processa la coda invii marketing + genera richiami automatici.'

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

        self._genera_richiami(cfg, dry)
        self._processa_coda(cfg, dry, max_batch)

    # ------------------------------------------------------------------
    # F5: richiamo automatico
    # ------------------------------------------------------------------

    def _genera_richiami(self, cfg, dry):
        if not cfg.richiamo_automatico_attivo:
            self.stdout.write('Richiamo automatico: OFF, skip.')
            return
        if not cfg.richiamo_template_meta:
            self.stdout.write(self.style.WARNING(
                'Richiamo automatico ON ma template Meta non configurato: skip.'))
            return

        oggi = timezone.localtime(timezone.now()).date()
        target = oggi - timedelta(days=cfg.richiamo_giorni_dopo)

        # Clienti il cui ULTIMO lavaggio completato cade nel giorno target.
        # Confronto sul giorno (range aware) per essere robusti ai fusi.
        candidati = (
            Cliente.objects
            .filter(ordine__stato='completato')
            .annotate(ultimo=Max('ordine__data_ora'))
            .filter(ultimo__date=target)
        )
        if not candidati.exists():
            self.stdout.write(f'Richiamo: nessun cliente con ultimo lavaggio il {target}.')
            return

        # Dry-run: nessuna scrittura, nemmeno il contenitore campagna.
        if dry:
            for c in candidati:
                esito = verifica_eleggibilita(c, cfg)
                self.stdout.write(
                    f'[dry-run] richiamo {c} -> '
                    f'{"in_coda" if esito.eleggibile else "saltato: " + esito.motivo}'
                )
            return

        # Campagna mensile: raggruppa i richiami del mese. Con
        # richiamo_giorni_dopo >= 30, un cliente non puo' rientrare due
        # volte nello stesso mese, quindi unique_together non confligge.
        nome_campagna = f'Richiamo automatico {oggi:%Y-%m}'
        campagna, _ = Campagna.objects.get_or_create(
            nome=nome_campagna,
            tipo='automatica_richiamo',
            defaults={
                'stato': 'in_corso',
                'template_meta': cfg.richiamo_template_meta,
                'template_params': ['{nome}', '{giorni_ultimo_lavaggio}'],
                'segmento_origine': 'richiamo_automatico',
                'finestra_conversione_giorni': cfg.giorni_finestra_conversione,
                'lanciata_il': timezone.now(),
            },
        )
        # Il template puo' cambiare nelle impostazioni: allinea la
        # campagna mensile corrente.
        if campagna.template_meta != cfg.richiamo_template_meta:
            campagna.template_meta = cfg.richiamo_template_meta
            campagna.save(update_fields=['template_meta'])

        accodati = 0
        for c in candidati:
            if InvioCampagna.objects.filter(campagna=campagna, cliente=c).exists():
                continue  # gia' accodato in un run precedente di oggi
            esito = verifica_eleggibilita(c, cfg, escludi_campagna=campagna)
            InvioCampagna.objects.create(
                campagna=campagna,
                cliente=c,
                stato='in_coda' if esito.eleggibile else 'saltato',
                motivo_salto='' if esito.eleggibile else esito.motivo,
            )
            accodati += 1
        if accodati:
            self.stdout.write(self.style.SUCCESS(
                f'Richiamo: {accodati} clienti accodati a "{nome_campagna}".'))

    # ------------------------------------------------------------------
    # F4: coda invii scaglionati
    # ------------------------------------------------------------------

    def _processa_coda(self, cfg, dry, max_batch):
        # Budget giornaliero: max_invii_giorno - inviati oggi (tutte le campagne)
        inizio_oggi = timezone.localtime(timezone.now()).replace(
            hour=0, minute=0, second=0, microsecond=0)
        inviati_oggi = InvioCampagna.objects.filter(
            stato='inviato', inviato_il__gte=inizio_oggi).count()
        budget = max(0, cfg.max_invii_giorno - inviati_oggi)
        if budget == 0:
            self.stdout.write(
                f'Tetto giornaliero raggiunto ({inviati_oggi}/{cfg.max_invii_giorno}): stop.')
            return
        batch = min(budget, max_batch)

        in_coda = (
            InvioCampagna.objects
            .filter(stato='in_coda', campagna__stato__in=['in_coda', 'in_corso'])
            .select_related('campagna', 'cliente')
            .order_by('campagna__lanciata_il', 'pk')[:batch]
        )
        if not in_coda:
            self.stdout.write('Coda invii vuota.')
            return

        self.stdout.write(
            f'Processo {len(in_coda)} invii '
            f'(budget giornaliero residuo {budget}, batch {max_batch}).')

        from apps.clients import whatsapp as wa
        from apps.messaggi.models import MessaggioWhatsApp

        for invio in in_coda:
            campagna = invio.campagna
            cliente = invio.cliente

            # La prima send di una campagna la promuove a in_corso
            if campagna.stato == 'in_coda' and not dry:
                campagna.stato = 'in_corso'
                campagna.save(update_fields=['stato'])

            # Re-check eleggibilita': opt-out o ricontatto potrebbero
            # essere sopraggiunti dopo il lancio della campagna.
            esito = verifica_eleggibilita(cliente, cfg, escludi_campagna=campagna)
            if not esito.eleggibile:
                if dry:
                    self.stdout.write(f'[dry-run] {cliente}: SALTATO ({esito.motivo})')
                else:
                    invio.stato = 'saltato'
                    invio.motivo_salto = esito.motivo
                    invio.save(update_fields=['stato', 'motivo_salto'])
                continue

            to_e164 = wa._to_e164(cliente.telefono)
            if not to_e164:
                if not dry:
                    invio.stato = 'saltato'
                    invio.motivo_salto = 'telefono non valido'
                    invio.save(update_fields=['stato', 'motivo_salto'])
                continue

            params = risolvi_params(campagna.template_params, cliente)

            if dry:
                self.stdout.write(
                    f'[dry-run] {cliente} <- template={campagna.template_meta} '
                    f'params={params}')
                continue

            ok, wa_id = wa._send_template_blocking(
                to_e164, campagna.template_meta, params)

            if ok:
                invio.stato = 'inviato'
                invio.inviato_il = timezone.now()
                # _send_template_blocking ha gia' creato il MessaggioWhatsApp
                # (via _log_outgoing_msg): lo agganciamo per leggere lo
                # stato di consegna in dashboard.
                if wa_id:
                    invio.messaggio_wa = MessaggioWhatsApp.objects.filter(
                        wa_message_id=wa_id).first()
                invio.save(update_fields=['stato', 'inviato_il', 'messaggio_wa'])
                self.stdout.write(self.style.SUCCESS(f'Inviato a {cliente}'))
            else:
                invio.stato = 'fallito'
                invio.save(update_fields=['stato'])
                self.stdout.write(self.style.ERROR(f'FALLITO invio a {cliente}'))

            # Pausa casuale anti-pattern-detection (non dopo l'ultimo)
            if invio != in_coda[len(in_coda) - 1]:
                pausa = random.randint(
                    cfg.intervallo_min_secondi, cfg.intervallo_max_secondi)
                self.stdout.write(f'  pausa {pausa}s...')
                time.sleep(pausa)

        # Chiudi le campagne manuali esaurite. Le campagne mensili di
        # richiamo restano in_corso: ricevono nuovi invii ogni giorno.
        if not dry:
            for campagna in Campagna.objects.filter(stato='in_corso', tipo='manuale'):
                if not campagna.invii.filter(stato='in_coda').exists():
                    campagna.stato = 'completata'
                    campagna.completata_il = timezone.now()
                    campagna.save(update_fields=['stato', 'completata_il'])
                    self.stdout.write(self.style.SUCCESS(
                        f'Campagna "{campagna.nome}" completata.'))
