from django.db import models
from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Scelte
# ---------------------------------------------------------------------------

class Postazione(models.TextChoices):
    POST1 = 'post1', 'Postazione 1 — Pre-lavaggio'
    POST2 = 'post2', 'Postazione 2 — Spazzole'
    POST3 = 'post3', 'Postazione 3 — Aspirazione'
    POST4 = 'post4', 'Postazione 4 — Plastiche e vetri'
    CONTROLLO_FINALE = 'controllo_finale', 'Controllo finale'


class Rilevatore(models.TextChoices):
    RESPONSABILE = 'responsabile', 'Responsabile'
    VICE = 'vice', 'Vice (in sostituzione del responsabile)'
    CLIENTE = 'cliente', 'Cliente'
    TITOLARE = 'titolare', 'Titolare (controllo a campione)'


class EsitoCQ(models.TextChoices):
    OK = 'ok', 'OK — nessun difetto'
    NON_OK = 'non_ok', 'Non OK — difetti rilevati'


class StatoScheda(models.TextChoices):
    APERTA = 'aperta', 'Aperta (modificabile)'
    CHIUSA = 'chiusa', 'Chiusa (definitiva)'


class ZonaAuto(models.TextChoices):
    # Esterno
    CARROZZERIA = 'carrozzeria_aloni_residui', 'Carrozzeria — aloni / gocce / residui'
    PARABREZZA_EST = 'parabrezza_esterno', 'Parabrezza esterno'
    LUNOTTO_EST = 'lunotto_esterno', 'Lunotto esterno'
    VETRI_LAT_EST = 'vetri_laterali_esterni', 'Vetri laterali esterni'
    SPECCHIETTI_EST = 'specchietti_esterni', 'Specchietti esterni sx e dx'
    CERCHI = 'cerchi', 'Cerchi'
    PASSARUOTA = 'passaruota', 'Passaruota'
    GOMME = 'gomme_nero_gomme', 'Gomme — nero gomme'
    PARAURTI = 'paraurti', 'Paraurti anteriore e posteriore'
    # Interni - plastiche
    CRUSCOTTO = 'cruscotto_plancia', 'Cruscotto e plancia'
    BOCCHETTE = 'bocchette_aria', 'Bocchette aria'
    TUNNEL = 'tunnel_centrale', 'Tunnel centrale'
    MONTANTI = 'montanti', 'Montanti'
    BATTITACCHI = 'battitacchi', 'Battitacchi'
    SEDILE_GUID = 'sedile_guidatore', 'Sedile guidatore'
    SEDILE_PASS = 'sedile_passeggero', 'Sedile passeggero'
    SEDILI_POST = 'sedili_posteriori', 'Sedili posteriori'
    VANI_PORTIERE = 'vani_portiere', 'Vani portiere'
    PANNELLI_PORTIERE = 'pannelli_portiere', 'Pannelli portiere'
    # Interni - moquette e tappeti
    MOQUETTE_ANT_SX = 'moquette_ant_sx', 'Moquette anteriore sx'
    MOQUETTE_ANT_DX = 'moquette_ant_dx', 'Moquette anteriore dx'
    MOQUETTE_POST_SX = 'moquette_post_sx', 'Moquette posteriore sx'
    MOQUETTE_POST_DX = 'moquette_post_dx', 'Moquette posteriore dx'
    TAPPETO_GUID = 'tappeto_guidatore', 'Tappeto guidatore'
    TAPPETO_PASS = 'tappeto_passeggero', 'Tappeto passeggero'
    # Vetri interni
    PARABREZZA_INT = 'parabrezza_interno', 'Parabrezza interno'
    LUNOTTO_INT = 'lunotto_interno', 'Lunotto interno'
    VETRI_LAT_INT = 'vetri_laterali_interni', 'Vetri laterali interni'
    SPECCHIETTO_INT = 'specchietto_retrovisore_int', 'Specchietto retrovisore interno'
    PARASOLI = 'parasoli', 'Parasoli'
    # Vani
    COFANO = 'cofano_interno', 'Cofano interno'
    BAGAGLIAIO = 'bagagliaio', 'Bagagliaio'
    CASSETTO_GUANTI = 'cassetto_guanti', 'Cassetto guanti'


class TipoDifetto(models.TextChoices):
    # Sporco residuo
    BRICIOLE = 'briciole_sabbia_polvere', 'Briciole / sabbia / polvere'
    FANGO = 'fango', 'Fango'
    MACCHIA_ORGANICA = 'macchia_organica', 'Macchia organica'
    ESCREMENTI = 'escrementi_insetti', 'Escrementi / insetti'
    # Trattamento non corretto
    ALONE_VETRO = 'alone_vetro', 'Alone su vetro'
    ALONE_PLASTICA = 'alone_plastica', 'Alone su plastica'
    PRODOTTO_NON_RIMOSSO = 'prodotto_non_rimosso', 'Prodotto non rimosso'
    NERO_GOMME_NON_UNIFORME = 'nero_gomme_non_uniforme', 'Nero gomme non uniforme'
    # Mancanza
    ZONA_NON_TRATTATA = 'zona_non_trattata', 'Zona non trattata'
    PROFUMO_MANCANTE = 'profumo_non_applicato', 'Profumo non applicato'
    FOGLIO_PROTETTIVO = 'foglio_protettivo_mancante', 'Foglio protettivo mancante'
    NERO_GOMME_MANCANTE = 'nero_gomme_mancante', 'Nero gomme mancante'
    # Danno
    GRAFFIO_CARR = 'graffio_carrozzeria', 'Graffio carrozzeria'
    GRAFFIO_PLASTICA = 'graffio_plastica', 'Graffio plastica interna'
    ALTRO = 'altro', 'Altro'


class Gravita(models.TextChoices):
    BASSA = 'bassa', 'Bassa — imperfezione minore'
    MEDIA = 'media', 'Media — difetto al controllo interno'
    ALTA = 'alta', 'Alta — cliente insoddisfatto / danno'


class AzioneCorrettiva(models.TextChoices):
    SISTEMATO = 'sistemato', 'Sistemato prima della consegna'
    CLIENTE_INFORMATO = 'cliente_informato', 'Cliente informato al momento della consegna'
    DA_RICHIAMARE = 'da_richiamare', 'Cliente già consegnato — da richiamare'
    DANNO_TITOLARE = 'danno_titolare', 'Danno — verificare con il titolare'


class TipoPunteggio(models.TextChoices):
    POSITIVO = 'positivo', 'Positivo (CQ senza difetti)'
    NEG_PRODUTTORE = 'negativo_produttore', 'Negativo — produttore del difetto'
    NEG_CATENA = 'negativo_catena', 'Negativo — catena (non ha intercettato)'
    NEG_RESPONSABILE = 'negativo_responsabile', 'Negativo — controllo finale fallito'
    MODIFICA_TITOLARE = 'modifica_titolare', 'Modifica manuale titolare'


# ---------------------------------------------------------------------------
# Modelli di configurazione (gestiti tramite pagina di configurazione CQ)
# ---------------------------------------------------------------------------

class CategoriaZona(models.Model):
    nome = models.CharField(max_length=100, unique=True, verbose_name='Nome')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Categoria zona'
        verbose_name_plural = 'Categorie zona'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome


class ZonaConfig(models.Model):
    categoria = models.ForeignKey(
        CategoriaZona, on_delete=models.CASCADE, related_name='zone',
        verbose_name='Categoria',
    )
    nome = models.CharField(max_length=100, verbose_name='Nome')
    codice = models.SlugField(max_length=80, unique=True, verbose_name='Codice')
    postazione_produttore = models.CharField(
        max_length=20, blank=True,
        verbose_name='Postazione produttore',
    )
    postazioni_catena = models.JSONField(
        default=list, blank=True,
        verbose_name='Postazioni catena',
        help_text='Postazioni intermedie che avrebbero dovuto intercettare il difetto',
    )
    attiva = models.BooleanField(default=True, verbose_name='Attiva')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Zona auto'
        verbose_name_plural = 'Zone auto'
        ordering = ['categoria__ordine', 'ordine', 'nome']

    def __str__(self):
        return f"{self.categoria.nome} — {self.nome}"


class CategoriaDifetto(models.Model):
    nome = models.CharField(max_length=100, unique=True, verbose_name='Nome')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Categoria difetto'
        verbose_name_plural = 'Categorie difetto'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome


class TipoDifettoConfig(models.Model):
    categoria = models.ForeignKey(
        CategoriaDifetto, on_delete=models.CASCADE, related_name='tipi',
        verbose_name='Categoria',
    )
    nome = models.CharField(max_length=100, verbose_name='Nome')
    codice = models.SlugField(max_length=80, unique=True, verbose_name='Codice')
    richiede_descrizione = models.BooleanField(
        default=False, verbose_name='Richiede descrizione',
        help_text='Se attivo, mostra un campo testo libero',
    )
    attivo = models.BooleanField(default=True, verbose_name='Attivo')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Tipo difetto'
        verbose_name_plural = 'Tipi difetto'
        ordering = ['categoria__ordine', 'ordine', 'nome']

    def __str__(self):
        return f"{self.categoria.nome} — {self.nome}"


class ZonaDifettoMapping(models.Model):
    zona = models.ForeignKey(
        ZonaConfig, on_delete=models.CASCADE, related_name='difetti_config',
        verbose_name='Zona',
    )
    tipo_difetto = models.ForeignKey(
        TipoDifettoConfig, on_delete=models.CASCADE, related_name='zone_config',
        verbose_name='Tipo difetto',
    )

    class Meta:
        verbose_name = 'Mapping zona-difetto'
        verbose_name_plural = 'Mapping zona-difetto'
        unique_together = [('zona', 'tipo_difetto')]

    def __str__(self):
        return f"{self.zona.nome} ↔ {self.tipo_difetto.nome}"


# ---------------------------------------------------------------------------
# Postazioni CQ (configurabili da DB)
# ---------------------------------------------------------------------------

class PostazioneCQ(models.Model):
    """
    Postazione di lavoro nel flusso CQ, gestibile dalla pagina di configurazione.
    Il campo codice mappa ai valori storici ('post1', 'post2', etc.).
    """
    codice = models.SlugField(max_length=20, unique=True, verbose_name='Codice')
    nome = models.CharField(max_length=100, verbose_name='Nome')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')
    attiva = models.BooleanField(default=True, verbose_name='Attiva')
    is_controllo_finale = models.BooleanField(
        default=False,
        verbose_name='Controllo finale',
        help_text='Segna questa postazione come controllo finale (una sola)',
    )
    postazione_fisica = models.ForeignKey(
        'core.Postazione', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='postazione_cq',
        verbose_name='Postazione fisica collegata',
        help_text='Collega alla postazione fisica per il routing ordini',
    )

    class Meta:
        verbose_name = 'Postazione CQ'
        verbose_name_plural = 'Postazioni CQ'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome


class BloccoPostazione(models.Model):
    """
    Sotto-blocco di lavoro all'interno di una postazione CQ.
    Opzionale: se una postazione non ha blocchi, l'operatore viene assegnato
    alla postazione intera.
    """
    postazione = models.ForeignKey(
        PostazioneCQ, on_delete=models.CASCADE, related_name='blocchi',
        verbose_name='Postazione',
    )
    codice = models.SlugField(max_length=40, unique=True, verbose_name='Codice')
    nome = models.CharField(max_length=100, verbose_name='Nome')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Blocco postazione'
        verbose_name_plural = 'Blocchi postazione'
        ordering = ['ordine', 'nome']
        unique_together = [('postazione', 'codice')]

    def __str__(self):
        return f"{self.postazione.nome} → {self.nome}"


# ---------------------------------------------------------------------------
# Configurazioni assegnazione operatori (preset)
# ---------------------------------------------------------------------------

class ConfigurazioneAssegnazione(models.Model):
    """
    Preset di assegnazione operatori alle postazioni/blocchi.
    Permette di precaricare la griglia operatori nella scheda CQ.
    """
    nome = models.CharField(max_length=100, verbose_name='Nome')
    attiva = models.BooleanField(default=True, verbose_name='Attiva')
    creato_da = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='configurazioni_assegnazione_create',
    )
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Configurazione assegnazione'
        verbose_name_plural = 'Configurazioni assegnazione'
        ordering = ['nome']

    def __str__(self):
        return self.nome


class AssegnazionePreset(models.Model):
    """
    Singola riga di assegnazione operatore→postazione/blocco nel preset.
    """
    configurazione = models.ForeignKey(
        ConfigurazioneAssegnazione, on_delete=models.CASCADE,
        related_name='assegnazioni',
    )
    postazione_codice = models.CharField(max_length=20, verbose_name='Postazione')
    blocco_codice = models.CharField(
        max_length=40, blank=True, default='',
        verbose_name='Blocco',
        help_text='Vuoto = intera postazione',
    )
    operatore = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='assegnazioni_preset',
    )

    class Meta:
        verbose_name = 'Assegnazione preset'
        verbose_name_plural = 'Assegnazioni preset'
        unique_together = [('configurazione', 'postazione_codice', 'blocco_codice', 'operatore')]

    def __str__(self):
        blocco = f" [{self.blocco_codice}]" if self.blocco_codice else ""
        return f"{self.configurazione.nome}: {self.postazione_codice}{blocco} → {self.operatore}"


# ---------------------------------------------------------------------------
# Helper: choices dinamiche da DB
# ---------------------------------------------------------------------------

def get_postazione_choices():
    """Restituisce le postazioni CQ dal DB, con fallback al TextChoices."""
    try:
        qs = PostazioneCQ.objects.filter(attiva=True).order_by('ordine')
        if qs.exists():
            return [(p.codice, p.nome) for p in qs]
    except Exception:
        pass
    return list(Postazione.choices)


def get_postazioni_ordinate():
    """Restituisce i codici delle postazioni CQ ordinati."""
    try:
        qs = PostazioneCQ.objects.filter(attiva=True).order_by('ordine')
        if qs.exists():
            return list(qs.values_list('codice', flat=True))
    except Exception:
        pass
    return [
        Postazione.POST1, Postazione.POST2, Postazione.POST3,
        Postazione.POST4, Postazione.CONTROLLO_FINALE,
    ]


def get_postazione_nome(codice):
    """Restituisce il nome leggibile di una postazione dato il codice."""
    try:
        obj = PostazioneCQ.objects.filter(codice=codice).first()
        if obj:
            return obj.nome
    except Exception:
        pass
    return dict(Postazione.choices).get(codice, codice)


# ---------------------------------------------------------------------------
# Modelli operativi
# ---------------------------------------------------------------------------

class OperatorePostazioneTurno(models.Model):
    """
    Registra quale operatore ha lavorato a quale postazione per un dato ordine.
    Compilato nell'intestazione della scheda CQ.
    """
    ordine = models.ForeignKey(
        'ordini.Ordine',
        on_delete=models.CASCADE,
        related_name='operatori_turno',
    )
    postazione = models.CharField(max_length=20)
    blocco_codice = models.CharField(
        max_length=40, blank=True, default='',
        verbose_name='Blocco',
        help_text='Vuoto = intera postazione',
    )
    operatore = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='turni_postazione',
    )

    class Meta:
        verbose_name = 'Operatore in turno'
        verbose_name_plural = 'Operatori in turno'
        unique_together = [('ordine', 'postazione', 'blocco_codice', 'operatore')]
        ordering = ['ordine', 'postazione']

    @property
    def postazione_nome(self):
        return get_postazione_nome(self.postazione)

    def __str__(self):
        nome = self.postazione_nome
        blocco = f" [{self.blocco_codice}]" if self.blocco_codice else ""
        op = self.operatore.get_full_name() or self.operatore.username
        return f"{self.ordine} — {nome}{blocco} — {op}"


class SchedaCQ(models.Model):
    """
    Scheda di controllo qualità, una per ordine.
    Può essere compilata da responsabile o titolare.
    Il titolare può sempre riaprirla e rettificarla.
    """
    ordine = models.OneToOneField(
        'ordini.Ordine',
        on_delete=models.CASCADE,
        related_name='scheda_cq',
    )
    rilevato_da = models.CharField(
        max_length=20,
        choices=Rilevatore.choices,
        verbose_name='Rilevato da',
    )
    compilata_da = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='schede_cq_compilate',
        verbose_name='Compilata da',
    )
    esito = models.CharField(max_length=10, choices=EsitoCQ.choices, verbose_name='Esito')
    data_ora = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, verbose_name='Note aggiuntive')
    stato = models.CharField(
        max_length=10,
        choices=StatoScheda.choices,
        default=StatoScheda.APERTA,
        verbose_name='Stato scheda',
    )

    class Meta:
        verbose_name = 'Scheda CQ'
        verbose_name_plural = 'Schede CQ'
        ordering = ['-data_ora']

    def __str__(self):
        return f"CQ #{self.ordine.numero_progressivo} — {self.get_esito_display()}"

    @property
    def num_difetti(self):
        return self.difetti.count()

    @property
    def ha_difetti(self):
        return self.difetti.exists()

    @property
    def ha_difetti_gravi(self):
        return self.difetti.filter(gravita=Gravita.ALTA).exists()


class DifettoCQ(models.Model):
    """
    Singolo difetto rilevato nella scheda CQ.
    Ogni difetto genera automaticamente i punteggi negativi.
    """
    scheda = models.ForeignKey(
        SchedaCQ,
        on_delete=models.CASCADE,
        related_name='difetti',
    )
    zona = models.CharField(max_length=80, verbose_name='Zona auto')
    tipo_difetto = models.CharField(max_length=80, verbose_name='Tipo di difetto')
    descrizione_altro = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Descrizione (se Altro)',
    )
    gravita = models.CharField(max_length=10, choices=Gravita.choices, verbose_name='Gravità')
    postazione_responsabile = models.CharField(
        max_length=20,
        verbose_name='Postazione responsabile',
    )
    azione_correttiva = models.CharField(
        max_length=20,
        choices=AzioneCorrettiva.choices,
        verbose_name='Azione correttiva',
    )
    foto = models.ImageField(
        upload_to='cq/foto/',
        blank=True,
        null=True,
        verbose_name='Foto',
    )
    note = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Difetto CQ'
        verbose_name_plural = 'Difetti CQ'

    @property
    def zona_nome(self):
        obj = ZonaConfig.objects.filter(codice=self.zona).first()
        return obj.nome if obj else self.zona

    @property
    def tipo_difetto_nome(self):
        obj = TipoDifettoConfig.objects.filter(codice=self.tipo_difetto).first()
        return obj.nome if obj else self.tipo_difetto

    @property
    def postazione_responsabile_nome(self):
        return get_postazione_nome(self.postazione_responsabile)

    def __str__(self):
        return f"{self.zona_nome} — {self.get_gravita_display()}"


class PunteggioCQ(models.Model):
    """
    Audit trail dei punti assegnati (positivi o negativi) da ogni scheda CQ.
    Generato automaticamente — non modificare manualmente.
    """
    scheda = models.ForeignKey(
        SchedaCQ,
        on_delete=models.CASCADE,
        related_name='punteggi',
    )
    difetto = models.ForeignKey(
        DifettoCQ,
        on_delete=models.CASCADE,
        related_name='punteggi',
        null=True,
        blank=True,
        help_text='Null se punteggio positivo (CQ OK)',
    )
    operatore = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='punteggi_cq',
    )
    punti = models.IntegerField()
    tipo = models.CharField(max_length=30, choices=TipoPunteggio.choices)
    mese = models.PositiveSmallIntegerField()
    anno = models.PositiveSmallIntegerField()
    motivazione = models.TextField(blank=True)
    data_ora = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Punteggio CQ'
        verbose_name_plural = 'Punteggi CQ'
        ordering = ['-data_ora']

    def __str__(self):
        segno = '+' if self.punti >= 0 else ''
        return f"{self.operatore.username} {segno}{self.punti} — {self.get_tipo_display()}"


class ModificaPunteggio(models.Model):
    """
    Modifica manuale del punteggio da parte del titolare.
    Motivazione scritta obbligatoria per garantire trasparenza.
    """
    operatore = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='modifiche_punteggio_ricevute',
    )
    anno = models.PositiveSmallIntegerField()
    mese = models.PositiveSmallIntegerField()
    punti = models.IntegerField(help_text='Valore positivo o negativo')
    motivazione = models.TextField(verbose_name='Motivazione (obbligatoria)')
    creato_da = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='modifiche_punteggio_create',
    )
    data_ora = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Modifica punteggio'
        verbose_name_plural = 'Modifiche punteggio'
        ordering = ['-data_ora']

    def __str__(self):
        segno = '+' if self.punti >= 0 else ''
        return f"{self.operatore.username} {segno}{self.punti} ({self.mese}/{self.anno}) — {self.creato_da.username}"


class ImpostazionePremioMensile(models.Model):
    """
    Monte premi mensile stabilito dai titolari a inizio anno.
    """
    anno = models.PositiveSmallIntegerField()
    mese = models.PositiveSmallIntegerField()
    monte_premi = models.DecimalField(max_digits=8, decimal_places=2, verbose_name='Monte premi (€)')
    note = models.TextField(blank=True)
    creato_da = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='impostazioni_premio_create',
    )
    creato_il = models.DateTimeField(auto_now_add=True)
    validato = models.BooleanField(default=False, help_text='Il titolare ha congelato il mese')
    validato_da = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='premi_validati',
    )
    validato_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Impostazione premio mensile'
        verbose_name_plural = 'Impostazioni premio mensile'
        unique_together = [('anno', 'mese')]
        ordering = ['-anno', '-mese']

    def __str__(self):
        return f"Premio {self.mese}/{self.anno} — €{self.monte_premi}"
