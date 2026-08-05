"""Modulo task interno per gli operatori, stile Todoist.

- Progetti ed etichette organizzano le task; entrambi liberi per tutto
  lo staff (scelta esplicita: gestione collaborativa, non top-down).
- Una task puo' essere visibile a tutti gli operatori oppure riservata
  (solo creatore + assegnatari): il filtro passa SEMPRE da
  Task.objects.visibile_a(user), mai query dirette nelle viste.
- Sotto-task = Task con parent (un solo livello nell'UI, il modello
  non lo vieta); i commenti sono un modello a parte.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class Progetto(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    colore = models.CharField(
        max_length=7, default='#6c757d',
        help_text='Colore hex del pallino progetto (es. #0d6efd).')
    ordine = models.PositiveIntegerField(default=0)
    archiviato = models.BooleanField(
        default=False,
        help_text='Un progetto archiviato sparisce dalla sidebar e dai '
                  'form, ma le sue task restano consultabili.')
    creato_da = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='progetti_task_creati')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ordine', 'nome']
        verbose_name = 'Progetto'
        verbose_name_plural = 'Progetti'

    def __str__(self):
        return self.nome


class Etichetta(models.Model):
    nome = models.CharField(max_length=50, unique=True)
    colore = models.CharField(max_length=7, default='#0d6efd')

    class Meta:
        ordering = ['nome']
        verbose_name = 'Etichetta'
        verbose_name_plural = 'Etichette'

    def __str__(self):
        return self.nome


class TaskQuerySet(models.QuerySet):
    def visibile_a(self, user):
        """'tutti' -> visibile a tutto lo staff; 'riservata' -> solo
        creatore e assegnatari. Il superuser vede tutto."""
        if user.is_superuser:
            return self
        return self.filter(
            Q(visibilita=Task.VIS_TUTTI)
            | Q(creato_da=user)
            | Q(assegnatari=user)
        ).distinct()

    def aperte(self):
        return self.filter(completata=False)

    def radice(self):
        return self.filter(parent__isnull=True)


class Task(models.Model):
    P1, P2, P3, P4 = 1, 2, 3, 4
    PRIORITA_CHOICES = [
        (P1, 'P1 - Urgente'),
        (P2, 'P2 - Alta'),
        (P3, 'P3 - Media'),
        (P4, 'P4 - Normale'),
    ]
    VIS_TUTTI = 'tutti'
    VIS_RISERVATA = 'riservata'
    VISIBILITA_CHOICES = [
        (VIS_TUTTI, 'Visibile a tutti'),
        (VIS_RISERVATA, 'Riservata (solo creatore e assegnatari)'),
    ]

    titolo = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True, default='')
    progetto = models.ForeignKey(
        Progetto, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tasks')
    # Sotto-task: CASCADE voluto — eliminare la task madre elimina le figlie.
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='sottotask')
    priorita = models.PositiveSmallIntegerField(
        choices=PRIORITA_CHOICES, default=P4)
    scadenza = models.DateTimeField(null=True, blank=True)
    assegnatari = models.ManyToManyField(
        User, blank=True, related_name='task_assegnate')
    etichette = models.ManyToManyField(
        Etichetta, blank=True, related_name='tasks')
    visibilita = models.CharField(
        max_length=10, choices=VISIBILITA_CHOICES, default=VIS_TUTTI)

    creato_da = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='task_create')
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    completata = models.BooleanField(default=False)
    completata_il = models.DateTimeField(null=True, blank=True)
    completata_da = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='task_completate')
    ordine = models.PositiveIntegerField(default=0)

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ['completata', 'priorita',
                    F('scadenza').asc(nulls_last=True), '-creato_il']
        indexes = [
            models.Index(fields=['completata', 'scadenza']),
            models.Index(fields=['progetto', 'completata']),
            models.Index(fields=['parent']),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(priorita__gte=1) & Q(priorita__lte=4),
                name='task_priorita_1_4'),
        ]
        verbose_name = 'Task'
        verbose_name_plural = 'Task'

    def __str__(self):
        return self.titolo

    @property
    def n_sottotask_tot(self) -> int:
        # len() sulla cache del prefetch: niente query extra per riga
        return len(self.sottotask.all())

    @property
    def n_sottotask_fatte(self) -> int:
        return sum(1 for s in self.sottotask.all() if s.completata)

    @property
    def in_ritardo(self) -> bool:
        return bool(not self.completata and self.scadenza
                    and self.scadenza < timezone.now())

    def marca_completata(self, user):
        self.completata = True
        self.completata_il = timezone.now()
        self.completata_da = user if getattr(user, 'pk', None) else None
        self.save(update_fields=['completata', 'completata_il',
                                 'completata_da', 'aggiornato_il'])

    def riapri(self):
        self.completata = False
        self.completata_il = None
        self.completata_da = None
        self.save(update_fields=['completata', 'completata_il',
                                 'completata_da', 'aggiornato_il'])

    def modificabile_da(self, user) -> bool:
        """Modifica/eliminazione: creatore, assegnatario, titolare o
        superuser. Il completamento invece e' aperto a chiunque veda la
        task (in un autolavaggio chi la finisce la spunta)."""
        return bool(
            user.is_superuser
            or self.creato_da_id == user.pk
            or self.assegnatari.filter(pk=user.pk).exists()
            or user.groups.filter(name='titolare').exists()
        )


class CommentoTask(models.Model):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='commenti')
    autore = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='commenti_task')
    testo = models.TextField()
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creato_il']
        verbose_name = 'Commento task'
        verbose_name_plural = 'Commenti task'

    def __str__(self):
        return f'Commento su "{self.task}"'
