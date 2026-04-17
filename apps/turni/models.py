from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Sessione turno
# ---------------------------------------------------------------------------

class SessioneTurno(models.Model):
    STATO_CHOICES = [
        ('attivo', 'Attivo'),
        ('chiuso', 'Chiuso'),
    ]
    operatore = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='sessioni_turno',
    )
    data_inizio = models.DateTimeField(auto_now_add=True)
    data_fine = models.DateTimeField(null=True, blank=True)
    stato = models.CharField(max_length=10, choices=STATO_CHOICES, default='attivo')

    class Meta:
        verbose_name = 'Sessione turno'
        verbose_name_plural = 'Sessioni turno'
        ordering = ['-data_inizio']

    def __str__(self):
        op = self.operatore.get_full_name() or self.operatore.username
        return f"Turno {op} — {self.data_inizio.strftime('%d/%m/%Y %H:%M')}"

    def chiudi(self):
        """Chiude la sessione e completa eventuali lavorazioni in corso."""
        for lav in self.lavorazioni.filter(stato__in=['in_lavorazione', 'in_pausa']):
            lav.completa()
        self.stato = 'chiuso'
        self.data_fine = timezone.now()
        self.save(update_fields=['stato', 'data_fine'])

    @property
    def checklist_inizio_compilata(self):
        return self.checklist.filter(fase='inizio').exists()

    @property
    def checklist_fine_compilata(self):
        return self.checklist.filter(fase='fine').exists()


# ---------------------------------------------------------------------------
# Postazione turno (postazioni scelte per il turno)
# ---------------------------------------------------------------------------

class PostazioneTurno(models.Model):
    sessione = models.ForeignKey(
        SessioneTurno, on_delete=models.CASCADE, related_name='postazioni',
    )
    postazione_cq = models.ForeignKey(
        'cq.PostazioneCQ', on_delete=models.PROTECT, related_name='turni',
    )
    blocco = models.ForeignKey(
        'cq.BloccoPostazione', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='turni',
    )

    class Meta:
        verbose_name = 'Postazione turno'
        verbose_name_plural = 'Postazioni turno'
        unique_together = [('sessione', 'postazione_cq', 'blocco')]

    def __str__(self):
        blocco_str = f" [{self.blocco.nome}]" if self.blocco else ""
        return f"{self.postazione_cq.nome}{blocco_str}"


# ---------------------------------------------------------------------------
# Checklist 5S — categorie e esiti configurabili
# ---------------------------------------------------------------------------

class CategoriaChecklist(models.Model):
    """Categoria 5S per la checklist (es. Strumenti, Pulizia, Ordine, ecc.)."""
    nome = models.CharField(max_length=100, verbose_name='Nome')
    icona = models.CharField(max_length=50, blank=True, default='',
        verbose_name='Icona', help_text='Classe Bootstrap icon, es. bi-wrench')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Categoria checklist'
        verbose_name_plural = 'Categorie checklist'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome


class EsitoChecklist(models.Model):
    """Esito possibile per una categoria checklist (configurabile dal titolare)."""
    categoria = models.ForeignKey(
        CategoriaChecklist, on_delete=models.CASCADE, related_name='esiti',
    )
    codice = models.SlugField(max_length=30, verbose_name='Codice')
    nome = models.CharField(max_length=50, verbose_name='Nome')
    colore = models.CharField(max_length=20, default='secondary',
        verbose_name='Colore', help_text='Classe Bootstrap: success, danger, warning, info, secondary')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Esito checklist'
        verbose_name_plural = 'Esiti checklist'
        ordering = ['categoria__ordine', 'ordine']
        unique_together = [('categoria', 'codice')]

    def __str__(self):
        return f"{self.categoria.nome}: {self.nome}"


# ---------------------------------------------------------------------------
# Checklist voci e compilazione
# ---------------------------------------------------------------------------

class ChecklistItem(models.Model):
    """Voce di checklist configurabile dal titolare, per postazione/blocco/categoria.
    Supporta sub-items tramite parent self-reference e scelta fase (apertura/chiusura)."""
    postazione_cq = models.ForeignKey(
        'cq.PostazioneCQ', on_delete=models.CASCADE, related_name='checklist_items',
    )
    blocco = models.ForeignKey(
        'cq.BloccoPostazione', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='checklist_items',
        help_text='Se vuoto, la voce vale per tutta la postazione',
    )
    categoria = models.ForeignKey(
        CategoriaChecklist, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='checklist_items',
        verbose_name='Categoria 5S',
    )
    parent = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.CASCADE, related_name='subitems',
        verbose_name='Item padre',
        help_text='Se impostato, questa voce e un subitem del padre',
    )
    nome = models.CharField(max_length=200, verbose_name='Voce checklist')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')
    attivo = models.BooleanField(default=True, verbose_name='Attivo')
    in_apertura = models.BooleanField(
        default=True, verbose_name='In apertura turno',
        help_text='Mostra questa voce nella checklist di inizio turno',
    )
    in_chiusura = models.BooleanField(
        default=True, verbose_name='In chiusura turno',
        help_text='Mostra questa voce nella checklist di fine turno',
    )

    class Meta:
        verbose_name = 'Voce checklist'
        verbose_name_plural = 'Voci checklist'
        ordering = ['postazione_cq__ordine', 'blocco__ordine', 'categoria__ordine', 'ordine', 'nome']

    def __str__(self):
        blocco = f" [{self.blocco.nome}]" if self.blocco else ""
        cat = f" ({self.categoria.nome})" if self.categoria else ""
        parent = f" << {self.parent.nome}" if self.parent else ""
        return f"{self.postazione_cq.nome}{blocco}{cat}: {self.nome}{parent}"

    @property
    def is_subitem(self):
        return self.parent_id is not None

    @property
    def has_subitems(self):
        return self.subitems.exists()


class ChecklistCompilata(models.Model):
    """Compilazione di una voce checklist a inizio o fine turno."""
    FASE_CHOICES = [
        ('inizio', 'Inizio turno'),
        ('fine', 'Fine turno'),
    ]
    ESITO_CHOICES = [
        ('ok', 'OK'),
        ('non_ok', 'Non OK'),
        ('na', 'N/A'),
    ]
    sessione = models.ForeignKey(
        SessioneTurno, on_delete=models.CASCADE, related_name='checklist',
    )
    checklist_item = models.ForeignKey(
        ChecklistItem, on_delete=models.PROTECT, related_name='compilazioni',
    )
    fase = models.CharField(max_length=10, choices=FASE_CHOICES)
    esito = models.CharField(max_length=10, choices=ESITO_CHOICES, default='ok')
    esito_obj = models.ForeignKey(
        EsitoChecklist, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='compilazioni', verbose_name='Esito (5S)',
    )
    note = models.TextField(blank=True)
    compilato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Checklist compilata'
        verbose_name_plural = 'Checklist compilate'
        unique_together = [('sessione', 'checklist_item', 'fase')]

    def __str__(self):
        esito_nome = self.esito_obj.nome if self.esito_obj else self.get_esito_display()
        return f"{self.checklist_item.nome} — {self.get_fase_display()} — {esito_nome}"

    @property
    def is_problema(self):
        """True se l'esito non e OK e non e N/A (indica un problema da evidenziare)."""
        if self.esito_obj:
            return self.esito_obj.codice not in ('ok', 'na')
        return self.esito == 'non_ok'


class VerificaChecklist(models.Model):
    """Verifica a campione effettuata da responsabile/titolare su una compilazione."""
    ESITO_CHOICES = [
        ('confermato', 'Confermato'),
        ('non_conforme', 'Non conforme'),
    ]
    compilata = models.ForeignKey(
        ChecklistCompilata, on_delete=models.CASCADE, related_name='verifiche',
    )
    verificato_da = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='verifiche_checklist',
    )
    esito_verifica = models.CharField(max_length=20, choices=ESITO_CHOICES)
    note = models.TextField(blank=True)
    data_verifica = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Verifica checklist'
        verbose_name_plural = 'Verifiche checklist'
        ordering = ['-data_verifica']

    def __str__(self):
        return f"Verifica {self.compilata.checklist_item.nome} — {self.get_esito_verifica_display()}"


class SegnalazioneDifetto(models.Model):
    """
    Segnalazione difetto rilevato dall'operatore in qualita di CONTROLLORE (catena).
    L'operatore ha verificato in ingresso, ha trovato un difetto prodotto da un'altra
    postazione, lo segnala e lo corregge. Il difetto viene attribuito al produttore.
    L'anello di catena ha funzionato correttamente.
    """
    GRAVITA_CHOICES = [
        ('bassa', 'Bassa'),
        ('media', 'Media'),
        ('alta', 'Alta'),
    ]
    AZIONE_CHOICES = [
        ('corretto', 'Corretto prima della consegna'),
        ('segnalato', 'Solo segnalato (non corretto)'),
    ]
    ordine = models.ForeignKey(
        'ordini.Ordine', on_delete=models.CASCADE, related_name='segnalazioni_difetti',
    )
    zona = models.CharField(max_length=80, verbose_name='Zona auto')
    tipo_difetto = models.CharField(max_length=80, verbose_name='Tipo difetto')
    gravita = models.CharField(max_length=10, choices=GRAVITA_CHOICES, default='media')
    azione = models.CharField(max_length=20, choices=AZIONE_CHOICES, default='corretto',
        verbose_name='Azione intrapresa')
    # Postazione che ha PRODOTTO il difetto (responsabile)
    postazione_produttore = models.CharField(max_length=40, blank=True,
        verbose_name='Postazione produttore',
        help_text='Codice della postazione responsabile del difetto')
    # Postazione dell'operatore che ha RILEVATO il difetto (controllore catena)
    postazione_cq = models.ForeignKey(
        'cq.PostazioneCQ', on_delete=models.PROTECT,
        verbose_name='Rilevato da (postazione)',
    )
    operatore = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='segnalazioni_difetti',
        verbose_name='Rilevato da (operatore)',
    )
    note = models.TextField(blank=True)
    data_ora = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Segnalazione difetto'
        verbose_name_plural = 'Segnalazioni difetti'
        ordering = ['-data_ora']

    def __str__(self):
        return f"Difetto {self.zona} — {self.tipo_difetto} — Ordine {self.ordine.numero_progressivo}"

    @property
    def zona_nome(self):
        from apps.cq.models import ZonaConfig
        obj = ZonaConfig.objects.filter(codice=self.zona).first()
        return obj.nome if obj else self.zona

    @property
    def tipo_difetto_nome(self):
        from apps.cq.models import TipoDifettoConfig
        obj = TipoDifettoConfig.objects.filter(codice=self.tipo_difetto).first()
        return obj.nome if obj else self.tipo_difetto


# ---------------------------------------------------------------------------
# Lavorazione operatore (tracking tempo per operatore/ordine/fase)
# ---------------------------------------------------------------------------

class LavorazioneOperatore(models.Model):
    STATO_CHOICES = [
        ('in_lavorazione', 'In Lavorazione'),
        ('in_pausa', 'In Pausa'),
        ('completato', 'Completato'),
    ]
    sessione = models.ForeignKey(
        SessioneTurno, on_delete=models.CASCADE, related_name='lavorazioni',
    )
    ordine = models.ForeignKey(
        'ordini.Ordine', on_delete=models.CASCADE, related_name='lavorazioni_operatore',
    )
    postazione_cq = models.ForeignKey(
        'cq.PostazioneCQ', on_delete=models.PROTECT,
    )
    blocco = models.ForeignKey(
        'cq.BloccoPostazione', null=True, blank=True, on_delete=models.SET_NULL,
    )
    inizio = models.DateTimeField(default=timezone.now)
    fine = models.DateTimeField(null=True, blank=True)
    pausa_inizio = models.DateTimeField(null=True, blank=True)
    tempo_pausa_totale = models.DurationField(default=timedelta)
    stato = models.CharField(
        max_length=20, choices=STATO_CHOICES, default='in_lavorazione',
    )

    class Meta:
        verbose_name = 'Lavorazione operatore'
        verbose_name_plural = 'Lavorazioni operatore'
        ordering = ['-inizio']

    def __str__(self):
        op = self.sessione.operatore.get_full_name() or self.sessione.operatore.username
        return f"{op} — Ordine {self.ordine.numero_progressivo} — {self.postazione_cq.nome}"

    @property
    def tempo_lavoro_netto(self):
        """Tempo netto di lavorazione escludendo le pause."""
        fine = self.fine or timezone.now()
        durata_totale = fine - self.inizio
        return durata_totale - self.tempo_pausa_totale

    @property
    def tempo_lavoro_netto_minuti(self):
        """Tempo netto in minuti (float)."""
        return self.tempo_lavoro_netto.total_seconds() / 60

    def avvia_pausa(self):
        if self.stato == 'in_lavorazione':
            self.stato = 'in_pausa'
            self.pausa_inizio = timezone.now()
            self.save(update_fields=['stato', 'pausa_inizio'])

    def riprendi(self):
        if self.stato == 'in_pausa' and self.pausa_inizio:
            durata_pausa = timezone.now() - self.pausa_inizio
            self.tempo_pausa_totale += durata_pausa
            self.pausa_inizio = None
            self.stato = 'in_lavorazione'
            self.save(update_fields=['stato', 'pausa_inizio', 'tempo_pausa_totale'])

    def completa(self):
        if self.stato == 'in_pausa':
            self.riprendi()
        self.stato = 'completato'
        self.fine = timezone.now()
        self.save(update_fields=['stato', 'fine'])
