"""Modulo Marketing/CRM: segmentazione clienti, campagne WhatsApp,
richiamo automatico e misurazione conversioni.

Design notes:
- La segmentazione NON e' persistita: viene calcolata al volo dal
  service `apps.marketing.services.segmentazione` leggendo lo storico
  Ordine. Con i volumi attuali (migliaia di clienti, decine di ordini
  a testa) il costo e' <1s; evita disallineamenti cache/DB. Se in
  futuro il volume cresce, si sposta il calcolo in un task periodico
  che salva su tabella.
- Gli invii NON duplicano il corpo del messaggio: il testo vive gia'
  in MessaggioWhatsApp (creato da _send_template_blocking via
  _log_outgoing_msg). InvioCampagna ci si collega via FK per lo stato
  di consegna (i webhook Meta aggiornano MessaggioWhatsApp.stato).
"""
import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone


class ImpostazioniMarketing(models.Model):
    """Singleton con tutte le soglie configurabili del modulo.

    Pattern singleton identico a ConfigurazionePianificazione
    (apps/ordini/models.py): save() forza pk=1.
    """

    # --- Segmentazione ---
    giorni_dormiente = models.PositiveSmallIntegerField(
        default=120,
        help_text="Nessun lavaggio da piu' di N giorni -> cliente 'dormiente'.",
    )
    giorni_rallentamento_delta = models.PositiveSmallIntegerField(
        default=30,
        help_text="Se i giorni dall'ultimo lavaggio superano la frequenza "
                  "media del cliente + questo delta -> 'in rallentamento'.",
    )

    # --- Scaglionamento invii ---
    max_invii_giorno = models.PositiveSmallIntegerField(
        default=40,
        help_text="Tetto massimo di messaggi promozionali inviabili in un "
                  "giorno (tutte le campagne sommate). Protegge il numero "
                  "WhatsApp Business da blocchi anti-spam Meta.",
    )
    intervallo_min_secondi = models.PositiveSmallIntegerField(
        default=45,
        help_text="Pausa minima tra due invii consecutivi (secondi).",
    )
    intervallo_max_secondi = models.PositiveSmallIntegerField(
        default=180,
        help_text="Pausa massima tra due invii consecutivi (secondi). "
                  "La pausa effettiva e' un valore casuale nel range.",
    )
    orario_invio_da = models.TimeField(
        default=datetime.time(9, 0),
        help_text="La coda invia messaggi promozionali solo a partire da "
                  "quest'ora (ora locale).",
    )
    orario_invio_a = models.TimeField(
        default=datetime.time(20, 0),
        help_text="La coda smette di inviare dopo quest'ora. Il bottone "
                  "'Invia ora' per singolo destinatario ignora la fascia "
                  "(scelta esplicita dell'operatore).",
    )

    def in_fascia_invio(self, ora=None) -> bool:
        """True se l'ora locale rientra nella fascia di invio consentita.
        Gestisce anche fasce a cavallo di mezzanotte (da > a)."""
        ora = ora or timezone.localtime(timezone.now()).time()
        da, a = self.orario_invio_da, self.orario_invio_a
        if da <= a:
            return da <= ora <= a
        return ora >= da or ora <= a

    # --- Anti-duplicati ---
    finestra_no_ricontatto_giorni = models.PositiveSmallIntegerField(
        default=30,
        help_text="Un cliente che ha ricevuto un messaggio promozionale "
                  "negli ultimi N giorni non viene ricontattato.",
    )

    # --- Richiamo automatico ---
    richiamo_automatico_attivo = models.BooleanField(
        default=False,
        help_text="Interruttore ON/OFF del richiamo automatico post-lavaggio.",
    )
    richiamo_giorni_dopo = models.PositiveSmallIntegerField(
        default=45,
        help_text="Invia il promemoria quando l'ultimo lavaggio risale a "
                  "N giorni fa.",
    )
    richiamo_template_meta = models.CharField(
        max_length=100, blank=True, default='',
        help_text="Nome del template Meta approvato per il richiamo "
                  "automatico (es. 'richiamo_45_giorni'). Vuoto = richiamo "
                  "disattivato anche se l'interruttore e' ON.",
    )

    # --- Misurazione ---
    giorni_finestra_conversione = models.PositiveSmallIntegerField(
        default=21,
        help_text="Un cliente 'converte' se torna a lavare entro N giorni "
                  "dal messaggio.",
    )

    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Impostazioni marketing'
        verbose_name_plural = 'Impostazioni marketing'

    def __str__(self):
        return 'Impostazioni marketing'

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SegmentoPersonalizzato(models.Model):
    """Segmento clienti definito dall'operatore con criteri numerici.

    Ogni criterio e' opzionale (vuoto = non filtrare): si combinano in
    AND. Il calcolo resta al volo come per i segmenti automatici
    (statistiche da Ordine completato, vedi services/segmentazione).
    Usabile come destinatario campagne con chiave 'custom:<pk>'.
    """

    TIPO_CLIENTE_CHOICES = [
        ('', 'Tutti'),
        ('privato', 'Solo privati'),
        ('azienda', 'Solo aziende'),
    ]

    nome = models.CharField(max_length=100)
    descrizione = models.CharField(max_length=200, blank=True, default='')
    attivo = models.BooleanField(default=True)

    # --- Criteri (tutti opzionali, in AND) ---
    lavaggi_min = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Totale lavaggi (min)')
    lavaggi_max = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Totale lavaggi (max)')
    giorni_ultimo_min = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Giorni da ultimo lavaggio (min)')
    giorni_ultimo_max = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Giorni da ultimo lavaggio (max)')
    frequenza_min = models.FloatField(
        null=True, blank=True, verbose_name='Frequenza media gg (min)',
        help_text='Clienti che tornano ogni ALMENO N giorni. I clienti '
                  'con 1 solo lavaggio sono esclusi dai filtri frequenza.')
    frequenza_max = models.FloatField(
        null=True, blank=True, verbose_name='Frequenza media gg (max)',
        help_text='Clienti che tornano ogni AL MASSIMO N giorni '
                  '(frequenti). Es. max 30 = almeno un lavaggio al mese.')
    spesa_totale_min = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        verbose_name='Spesa totale EUR (min)')
    spesa_totale_max = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True,
        verbose_name='Spesa totale EUR (max)')
    spesa_media_min = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        verbose_name='Spesa media a lavaggio EUR (min)')
    spesa_media_max = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        verbose_name='Spesa media a lavaggio EUR (max)')
    tipo_cliente = models.CharField(
        max_length=10, choices=TIPO_CLIENTE_CHOICES, blank=True, default='')

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Segmento personalizzato'
        verbose_name_plural = 'Segmenti personalizzati'

    def __str__(self):
        return self.nome

    @property
    def chiave(self) -> str:
        """Chiave usata nel composer campagne e in segmento_origine."""
        return f'custom:{self.pk}'

    def criteri_leggibili(self) -> list:
        """Riassunto testuale dei criteri attivi (per liste e card)."""
        out = []

        def intervallo(etichetta, minimo, massimo, unita=''):
            if minimo is not None and massimo is not None:
                out.append(f'{etichetta} {minimo}-{massimo}{unita}')
            elif minimo is not None:
                out.append(f'{etichetta} >= {minimo}{unita}')
            elif massimo is not None:
                out.append(f'{etichetta} <= {massimo}{unita}')

        intervallo('lavaggi', self.lavaggi_min, self.lavaggi_max)
        intervallo('giorni da ultimo', self.giorni_ultimo_min, self.giorni_ultimo_max)
        intervallo('frequenza', self.frequenza_min, self.frequenza_max, ' gg')
        intervallo('spesa tot.', self.spesa_totale_min, self.spesa_totale_max, ' EUR')
        intervallo('spesa media', self.spesa_media_min, self.spesa_media_max, ' EUR')
        if self.tipo_cliente:
            out.append(dict(self.TIPO_CLIENTE_CHOICES)[self.tipo_cliente])
        return out or ['nessun criterio (tutti i clienti con storico)']


class AnalisiAI(models.Model):
    """Un'analisi del consulente AI marketing (Claude API).

    Ogni generazione e' salvata: l'operatore puo' rileggere le analisi
    passate senza rispendere token. Le proposte (segmenti, campagne,
    promozioni) restano JSON: diventano oggetti veri solo quando
    l'operatore le approva coi bottoni della pagina AI — l'AI non
    invia MAI nulla da sola.
    """

    creata_il = models.DateTimeField(auto_now_add=True)
    creata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    modello = models.CharField(
        max_length=60, default='',
        help_text='Modello Claude usato per questa analisi.')

    # Cosa ha visto l'AI (solo aggregati anonimi) e cosa ha risposto.
    contesto = models.JSONField(default=dict)
    analisi = models.TextField(
        default='', help_text="Analisi generale dell'andamento marketing.")
    best_practice = models.JSONField(default=list)
    proposte_segmenti = models.JSONField(default=list)
    proposte_campagne = models.JSONField(default=list)
    promozioni = models.JSONField(default=list)

    # Trasparenza costi
    token_input = models.PositiveIntegerField(default=0)
    token_output = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-creata_il']
        verbose_name = 'Analisi AI'
        verbose_name_plural = 'Analisi AI'

    def __str__(self):
        return f'Analisi AI del {self.creata_il:%d/%m/%Y %H:%M}'

    @property
    def n_proposte(self) -> int:
        return (len(self.proposte_segmenti) + len(self.proposte_campagne)
                + len(self.promozioni))


class Campagna(models.Model):
    TIPO_CHOICES = [
        ('manuale', 'Manuale'),
        ('automatica_richiamo', 'Richiamo automatico'),
    ]
    STATO_CHOICES = [
        ('bozza', 'Bozza'),
        ('in_coda', 'In coda'),
        ('in_corso', 'In corso'),
        ('in_pausa', 'In pausa'),
        ('completata', 'Completata'),
        ('annullata', 'Annullata'),
    ]

    nome = models.CharField(max_length=200)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default='manuale')
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='bozza')

    # Template Meta approvato + mapping dei parametri body {{1}}, {{2}}, ...
    # Ogni elemento della lista e' un placeholder logico che il cron
    # risolve per-cliente: '{nome}', '{giorni_ultimo_lavaggio}',
    # '{totale_lavaggi}' oppure un testo fisso qualsiasi.
    template_meta = models.CharField(
        max_length=100,
        help_text="Nome del template Meta approvato da usare per l'invio.",
    )
    template_params = models.JSONField(
        default=list, blank=True,
        help_text='Parametri body del template in ordine. Placeholder '
                  'supportati: {nome}, {giorni_ultimo_lavaggio}, '
                  '{totale_lavaggi}. Esempio: ["{nome}", "20%"]',
    )

    # Snapshot del segmento al momento del lancio (lista di cliente_id).
    # Fotografato perche' la segmentazione e' dinamica: senza snapshot i
    # destinatari cambierebbero tra il lancio e l'ultimo invio scaglionato.
    segmento_origine = models.CharField(
        max_length=120, blank=True, default='',
        help_text="Segmenti da cui e' partita la selezione (informativo). "
                  "Piu' segmenti sono separati da virgola; 'tutti' = intera "
                  "anagrafica.",
    )
    finestra_conversione_giorni = models.PositiveSmallIntegerField(
        default=21,
        help_text="Copia della finestra conversione al momento del lancio.",
    )

    creata_il = models.DateTimeField(auto_now_add=True)
    lanciata_il = models.DateTimeField(null=True, blank=True)
    completata_il = models.DateTimeField(null=True, blank=True)
    creata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        ordering = ['-creata_il']
        verbose_name = 'Campagna'
        verbose_name_plural = 'Campagne'

    def __str__(self):
        return f'{self.nome} ({self.get_stato_display()})'

    # --- Statistiche (query al volo; per dashboard) ---

    @property
    def n_destinatari(self):
        return self.invii.count()

    @property
    def n_inviati(self):
        return self.invii.filter(stato='inviato').count()

    @property
    def n_falliti(self):
        return self.invii.filter(stato='fallito').count()

    @property
    def n_in_coda(self):
        return self.invii.filter(stato='in_coda').count()


class InvioCampagna(models.Model):
    """Un destinatario di una campagna. Stato del singolo invio.

    Il corpo del messaggio non e' duplicato qui: l'invio effettivo crea
    un MessaggioWhatsApp (via _log_outgoing_msg dentro
    _send_template_blocking) e ci si collega via FK. Lo stato di
    consegna Meta (sent/delivered/read/failed) si legge da li'.
    """
    STATO_CHOICES = [
        ('in_coda', 'In coda'),
        ('inviato', 'Inviato'),
        ('fallito', 'Fallito'),
        ('saltato', 'Saltato'),
    ]

    campagna = models.ForeignKey(
        Campagna, on_delete=models.CASCADE, related_name='invii',
    )
    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.CASCADE,
        related_name='invii_marketing',
    )
    messaggio_wa = models.ForeignKey(
        'messaggi.MessaggioWhatsApp', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    stato = models.CharField(max_length=12, choices=STATO_CHOICES, default='in_coda')
    inviato_il = models.DateTimeField(null=True, blank=True)
    motivo_salto = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Perche' l'invio e' stato saltato (opt-out, no telefono, "
                  "gia' contattato di recente, ...).",
    )
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('campagna', 'cliente')]
        indexes = [
            models.Index(fields=['stato', 'campagna']),
            models.Index(fields=['cliente', 'inviato_il']),
        ]
        verbose_name = 'Invio campagna'
        verbose_name_plural = 'Invii campagna'

    def __str__(self):
        return f'{self.campagna.nome} -> {self.cliente} [{self.stato}]'

    def ha_convertito(self) -> bool:
        """True se il cliente ha fatto un lavaggio completato entro la
        finestra di conversione dal momento dell'invio."""
        if not self.inviato_il:
            return False
        from apps.ordini.models import Ordine
        fine = self.inviato_il + timezone.timedelta(
            days=self.campagna.finestra_conversione_giorni
        )
        return Ordine.objects.filter(
            cliente=self.cliente,
            stato='completato',
            data_ora__gt=self.inviato_il,
            data_ora__lte=fine,
        ).exists()
