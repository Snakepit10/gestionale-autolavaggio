"""Garage: libretto tagliandi digitale + percorso manutenzione gamificato.

Design notes:
- La DEFINIZIONE del percorso (livelli, servizi, punti, premi, badge)
  vive tutta in tabelle di configurazione (Percorso, LivelloPercorso,
  TipoServizioGarage, ItemLivello) seminate da una data migration:
  niente hardcoded, modificabile da admin senza toccare codice. Ogni
  Veicolo punta a un Percorso: oggi tutti quello standard, domani
  percorsi personalizzati con la stessa struttura dati.
- Il punteggio salute usa il DECADIMENTO LAZY: `punteggio_salute` e'
  materializzato solo quando si scrive un evento (prima si applica il
  decadimento maturato, poi si sommano i punti); tra un evento e
  l'altro il valore mostrato e' la property `salute_attuale`, calcolo
  deterministico da (punteggio, salute_aggiornata_il). Scelta motivata:
  zero job schedulati in piu' (il cron serve solo alle notifiche),
  nessun rischio di drift, stesso numero per tutti i lettori.
- Idempotenza premi: doppia difesa — PremioVeicolo.chiave unique QUI
  + chiave_idempotenza sul wallet monete (UniqueConstraint parziale
  gia' esistente). Badge idempotenti via unique (veicolo, slug).
- Tutti i calcoli su giorni/scadenze usano date locali Europe/Rome
  (timezone.localtime), come il resto del progetto.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------
# Configurazione del percorso (seed nella data migration 0002)
# ---------------------------------------------------------------------

class Percorso(models.Model):
    """Un percorso di manutenzione (sequenza di livelli).

    'Percorso standard' e' quello predefinito assegnato ai nuovi
    veicoli; in futuro se ne possono creare di personalizzati e
    assegnarli a singoli veicoli.
    """
    nome = models.CharField(max_length=100, unique=True)
    predefinito = models.BooleanField(
        default=False,
        help_text='Assegnato automaticamente ai nuovi veicoli. '
                  'Deve essercene uno solo.')
    attivo = models.BooleanField(default=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Percorso'
        verbose_name_plural = 'Percorsi'

    def __str__(self):
        return self.nome

    @classmethod
    def standard(cls):
        return cls.objects.filter(predefinito=True, attivo=True).first()


class LivelloPercorso(models.Model):
    percorso = models.ForeignKey(
        Percorso, on_delete=models.CASCADE, related_name='livelli')
    numero = models.PositiveSmallIntegerField(
        help_text='Ordine di sblocco: 1, 2, 3, 4...')
    nome = models.CharField(max_length=100)
    premio_gettoni = models.PositiveSmallIntegerField(
        default=0,
        help_text='Gettoni accreditati (una sola volta per veicolo) al '
                  'completamento del livello.')
    badge_nome = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Badge di livello (vuoto = nessun badge).')
    badge_icona = models.CharField(
        max_length=50, blank=True, default='bi-award',
        help_text='Classe Bootstrap Icons (es. bi-award).')

    class Meta:
        ordering = ['percorso', 'numero']
        constraints = [
            models.UniqueConstraint(fields=['percorso', 'numero'],
                                    name='uq_livello_percorso_numero'),
        ]
        verbose_name = 'Livello percorso'
        verbose_name_plural = 'Livelli percorso'

    def __str__(self):
        return f'{self.percorso} - L{self.numero} {self.nome}'


class TipoServizioGarage(models.Model):
    """Catalogo dei tipi di servizio registrabili sul libretto.

    Indipendente dal percorso: un tipo puo' comparire in piu' percorsi
    (via ItemLivello) o in nessuno (es. self_service).
    """
    slug = models.SlugField(max_length=50, unique=True)
    nome = models.CharField(max_length=100)
    punti_salute = models.PositiveSmallIntegerField(
        default=0, help_text='Punti aggiunti al punteggio salute (0-100).')
    validita_giorni = models.PositiveSmallIntegerField(
        default=0,
        help_text='Durata del trattamento in giorni: alla scadenza '
                  'risulta "da rinnovare". 0 = nessuna scadenza.')
    badge_nome = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Badge milestone sbloccato alla prima registrazione '
                  'di questo servizio (vuoto = nessun badge).')
    badge_icona = models.CharField(max_length=50, blank=True, default='bi-star')
    attivo = models.BooleanField(default=True)
    ordine = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordine', 'nome']
        verbose_name = 'Tipo servizio garage'
        verbose_name_plural = 'Tipi servizio garage'

    def __str__(self):
        return self.nome


class ItemLivello(models.Model):
    """Un servizio nella checklist di un livello."""
    livello = models.ForeignKey(
        LivelloPercorso, on_delete=models.CASCADE, related_name='items')
    tipo_servizio = models.ForeignKey(
        TipoServizioGarage, on_delete=models.CASCADE, related_name='items_livello')
    soddisfatto_da_self = models.BooleanField(
        default=False,
        help_text='Se attivo, anche un lavaggio self service completa '
                  'questo item (es. lavaggio esterno).')
    ordine = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['livello', 'ordine']
        constraints = [
            models.UniqueConstraint(fields=['livello', 'tipo_servizio'],
                                    name='uq_item_livello_tipo'),
        ]
        verbose_name = 'Item livello'
        verbose_name_plural = 'Item livello'

    def __str__(self):
        return f'{self.livello} <- {self.tipo_servizio}'


class ImpostazioniGarage(models.Model):
    """Singleton (pattern get_solo come ImpostazioniMonete): tutti i
    parametri numerici della gamification, mai hardcoded."""

    punteggio_iniziale = models.PositiveSmallIntegerField(default=50)
    decadimento_giorni = models.PositiveSmallIntegerField(
        default=14, help_text='Ogni N giorni senza servizi il punteggio '
                              'scende di decadimento_punti.')
    decadimento_punti = models.PositiveSmallIntegerField(default=1)
    soglia_notifica_salute = models.PositiveSmallIntegerField(
        default=60, help_text='Sotto questa soglia parte l\'evento di '
                              'notifica "punteggio basso".')
    streak_finestra_giorni = models.PositiveSmallIntegerField(
        default=21, help_text='La streak resta viva con almeno un '
                              'servizio ogni N giorni.')
    streak_traguardi = models.JSONField(
        default=dict,
        help_text='Mappa {servizi_consecutivi: gettoni}. '
                  'Default: {"5": 1, "10": 3, "20": 6}.')
    giorni_avviso_scadenza = models.JSONField(
        default=list,
        help_text='Giorni prima della scadenza trattamento a cui '
                  'avvisare. Default: [30, 7].')
    giorni_avviso_streak = models.PositiveSmallIntegerField(
        default=3, help_text='Avviso streak in scadenza N giorni prima.')

    # Template Meta opzionali per le notifiche WhatsApp (vuoto = la
    # notifica resta solo un evento di dominio, nessun invio).
    template_livello_completato = models.CharField(max_length=100, blank=True, default='')
    template_badge_sbloccato = models.CharField(max_length=100, blank=True, default='')
    template_salute_bassa = models.CharField(max_length=100, blank=True, default='')
    template_streak_scadenza = models.CharField(max_length=100, blank=True, default='')
    template_trattamento_scadenza = models.CharField(max_length=100, blank=True, default='')

    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Impostazioni garage'
        verbose_name_plural = 'Impostazioni garage'

    def __str__(self):
        return 'Impostazioni garage'

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        if not self.streak_traguardi:
            self.streak_traguardi = {'5': 1, '10': 3, '20': 6}
        if not self.giorni_avviso_scadenza:
            self.giorni_avviso_scadenza = [30, 7]
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------------
# Dati per veicolo
# ---------------------------------------------------------------------

def normalizza_targa(raw: str) -> str:
    """Maiuscola, senza spazi ne' trattini (stessa logica del modulo
    abbonamenti: apps/abbonamenti/forms.py clean_targa)."""
    return (raw or '').upper().replace(' ', '').replace('-', '').strip()


class Veicolo(models.Model):
    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.CASCADE, related_name='veicoli')
    targa = models.CharField(
        max_length=10, help_text='Normalizzata maiuscola senza spazi.')
    marca = models.CharField(max_length=50)
    modello = models.CharField(max_length=80)
    # Foto prevista dallo schema ma NON esposta nella UI v1: i media su
    # Railway sono effimeri (nessun volume/S3). Si attiva in futuro.
    foto = models.ImageField(upload_to='garage/veicoli/', null=True, blank=True)
    percorso = models.ForeignKey(
        Percorso, null=True, on_delete=models.SET_NULL, related_name='veicoli')

    # --- Stato gamification (materializzato dal motore) ---
    punteggio_salute = models.PositiveSmallIntegerField(default=50)
    salute_aggiornata_il = models.DateTimeField(default=timezone.now)
    streak_conteggio = models.PositiveSmallIntegerField(default=0)
    streak_iniziata_il = models.DateTimeField(null=True, blank=True)
    streak_ultimo_evento = models.DateTimeField(null=True, blank=True)
    ultimo_uso_self = models.DateTimeField(
        null=True, blank=True,
        help_text='Per preselezionare il veicolo all\'avvio self service.')

    attivo = models.BooleanField(default=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creato_il']
        constraints = [
            models.UniqueConstraint(fields=['cliente', 'targa'],
                                    name='uq_veicolo_cliente_targa'),
        ]
        verbose_name = 'Veicolo'
        verbose_name_plural = 'Veicoli'

    def __str__(self):
        return f'{self.targa} ({self.marca} {self.modello})'

    def save(self, *args, **kwargs):
        self.targa = normalizza_targa(self.targa)
        super().save(*args, **kwargs)

    @property
    def salute_attuale(self) -> int:
        """Punteggio con il decadimento lazy applicato alla lettura."""
        cfg = ImpostazioniGarage.get_solo()
        if not cfg.decadimento_giorni:
            return self.punteggio_salute
        giorni = (timezone.localtime(timezone.now()).date()
                  - timezone.localtime(self.salute_aggiornata_il).date()).days
        decadi = (giorni // cfg.decadimento_giorni) * cfg.decadimento_punti
        return max(0, min(100, self.punteggio_salute - decadi))

    @property
    def streak_attiva(self) -> bool:
        """La streak e' viva se l'ultimo evento e' dentro la finestra."""
        if not self.streak_ultimo_evento or not self.streak_conteggio:
            return False
        cfg = ImpostazioniGarage.get_solo()
        giorni = (timezone.localtime(timezone.now()).date()
                  - timezone.localtime(self.streak_ultimo_evento).date()).days
        return giorni <= cfg.streak_finestra_giorni


class EventoLibretto(models.Model):
    """Una riga del libretto tagliandi del veicolo."""
    ORIGINE_CHOICES = [
        ('self_service', 'Self service'),
        ('operatore', 'Operatore'),
        ('importato', 'Importato'),
    ]

    veicolo = models.ForeignKey(
        Veicolo, on_delete=models.CASCADE, related_name='eventi')
    tipo_servizio = models.ForeignKey(
        TipoServizioGarage, on_delete=models.PROTECT, related_name='eventi')
    origine = models.CharField(max_length=15, choices=ORIGINE_CHOICES)
    data = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True, default='')
    foto = models.ImageField(  # come Veicolo.foto: schema si', UI v1 no
        upload_to='garage/eventi/', null=True, blank=True)
    registrato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    movimento = models.ForeignKey(
        'monete.MovimentoMoneta', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        help_text='Movimento gettoni dell\'avvio self service collegato.')
    # Predisposizione conferma via QR code a fine servizio (spec F3.3):
    # il token verra' generato/validato da una feature futura. Oggi
    # resta vuoto e nessuna logica lo usa.
    codice_conferma = models.CharField(max_length=64, blank=True, default='')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data']
        indexes = [
            models.Index(fields=['veicolo', '-data']),
        ]
        verbose_name = 'Evento libretto'
        verbose_name_plural = 'Eventi libretto'

    def __str__(self):
        return f'{self.veicolo.targa}: {self.tipo_servizio} ({self.data:%d/%m/%Y})'


class BadgeVeicolo(models.Model):
    """Badge sbloccato da un veicolo. unique (veicolo, slug) =
    idempotenza dello sblocco."""
    veicolo = models.ForeignKey(
        Veicolo, on_delete=models.CASCADE, related_name='badges')
    slug = models.SlugField(max_length=60)
    nome = models.CharField(max_length=100)
    icona = models.CharField(max_length=50, default='bi-award')
    ottenuto_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ottenuto_il']
        constraints = [
            models.UniqueConstraint(fields=['veicolo', 'slug'],
                                    name='uq_badge_veicolo_slug'),
        ]
        verbose_name = 'Badge veicolo'
        verbose_name_plural = 'Badge veicolo'

    def __str__(self):
        return f'{self.veicolo.targa}: {self.nome}'


class PremioVeicolo(models.Model):
    """Registro premi in gettoni accreditati a un veicolo.

    `chiave` unique = idempotenza applicativa (es. 'livello:<pk>',
    'streak:<iniziata YYYYMMDD>:<traguardo>'); la stessa chiave,
    prefissata 'garage:<veicolo_pk>:', va anche nel wallet monete come
    chiave_idempotenza (doppia difesa).
    """
    veicolo = models.ForeignKey(
        Veicolo, on_delete=models.CASCADE, related_name='premi')
    chiave = models.CharField(max_length=80)
    gettoni = models.PositiveSmallIntegerField()
    descrizione = models.CharField(max_length=200, blank=True, default='')
    movimento = models.ForeignKey(
        'monete.MovimentoMoneta', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creato_il']
        constraints = [
            models.UniqueConstraint(fields=['veicolo', 'chiave'],
                                    name='uq_premio_veicolo_chiave'),
        ]
        verbose_name = 'Premio veicolo'
        verbose_name_plural = 'Premi veicolo'

    def __str__(self):
        return f'{self.veicolo.targa}: {self.chiave} (+{self.gettoni})'


class NotificaGarage(models.Model):
    """Evento di dominio per le notifiche (registro + dedup).

    Il notifier scrive SEMPRE la riga; l'invio WhatsApp avviene solo se
    il template relativo e' configurato in ImpostazioniGarage. La
    coppia (veicolo, chiave_dedup) evita avvisi duplicati dal cron.
    """
    TIPO_CHOICES = [
        ('livello_completato', 'Livello completato'),
        ('badge_sbloccato', 'Badge sbloccato'),
        ('salute_bassa', 'Punteggio sotto soglia'),
        ('streak_scadenza', 'Streak in scadenza'),
        ('trattamento_scadenza', 'Trattamento in scadenza'),
    ]

    veicolo = models.ForeignKey(
        Veicolo, on_delete=models.CASCADE, related_name='notifiche')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    payload = models.JSONField(default=dict)
    chiave_dedup = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Evita doppi avvisi dal cron (es. '
                  '"scadenza:coating:2027-01-15:30").')
    inviata = models.BooleanField(
        default=False, help_text='True se il WhatsApp e\' partito.')
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creata_il']
        constraints = [
            models.UniqueConstraint(
                fields=['veicolo', 'chiave_dedup'],
                name='uq_notifica_veicolo_dedup',
                condition=~models.Q(chiave_dedup=''),
            ),
        ]
        verbose_name = 'Notifica garage'
        verbose_name_plural = 'Notifiche garage'

    def __str__(self):
        return f'{self.veicolo.targa}: {self.get_tipo_display()}'
