from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator
from decimal import Decimal


class Cassa(models.Model):
    """Anagrafica delle casse gestite dall'autolavaggio."""
    TIPO_CHOICES = [
        ('servito', 'Servito (ordini POS)'),
        ('automatica', 'Cassa automatica'),
    ]
    nome = models.CharField(max_length=100, verbose_name='Nome')
    numero = models.CharField(max_length=20, blank=True, verbose_name='Numero',
        help_text='Numero identificativo della cassa (es. 11057)')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    tracking_washcycles = models.BooleanField(default=False,
        verbose_name='Traccia WashCycles',
        help_text='Abilita il campo WashCycles nel form di chiusura')
    modalita_registratore = models.BooleanField(default=False,
        verbose_name='Modalita registratore (solo totale scontrino)',
        help_text='Se attivo, il form di chiusura mostra solo il totale scontrino (no vendite contante/non contante)')
    attiva = models.BooleanField(default=True)
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Cassa'
        verbose_name_plural = 'Casse'
        ordering = ['ordine', 'nome']

    def __str__(self):
        if self.numero:
            return f"{self.nome} (n. {self.numero})"
        return self.nome


class ChiusuraCassa(models.Model):
    """Gestisce l'apertura e chiusura giornaliera della cassa"""

    STATO_CHOICES = [
        ('aperta', 'Aperta'),
        ('chiusa', 'Chiusa'),
    ]

    # Identificazione
    data = models.DateField(default=timezone.now)
    operatore_apertura = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='aperture_cassa'
    )
    operatore_chiusura = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chiusure_cassa'
    )

    # Apertura
    data_ora_apertura = models.DateTimeField(auto_now_add=True)
    fondo_cassa_iniziale = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Contanti presenti all'apertura"
    )

    # Chiusura
    data_ora_chiusura = models.DateTimeField(null=True, blank=True)
    conteggio_cassa_reale = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Contanti effettivamente presenti alla chiusura"
    )

    # Calcoli automatici (aggiornati in tempo reale)
    totale_incassi_contanti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Totale incassi in contanti della giornata"
    )
    totale_pagamenti_contanti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Totale pagamenti in contanti (fornitori, spese)"
    )
    totale_prelievi = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Prelievi dal fondo cassa"
    )
    totale_versamenti = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Versamenti nel fondo cassa"
    )

    # Pagamenti non-contanti (per verifica incrociata)
    totale_carte = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_bancomat = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_bonifici = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    totale_altro = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Note e stato
    note_apertura = models.TextField(blank=True)
    note_chiusura = models.TextField(blank=True, help_text="Giustificazione differenze cassa")
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='aperta')

    # Conferma
    confermata = models.BooleanField(default=False, help_text="Chiusura confermata e bloccata")

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Chiusura Cassa"
        verbose_name_plural = "Chiusure Cassa"
        ordering = ['-data']
        unique_together = ['data']

    def __str__(self):
        return f"Cassa {self.data.strftime('%d/%m/%Y')} - {self.get_stato_display()}"

    @property
    def cassa_teorica_finale(self):
        """Calcola la cassa teorica finale"""
        return (
            self.fondo_cassa_iniziale +
            self.totale_incassi_contanti -
            self.totale_pagamenti_contanti +
            self.totale_versamenti -
            self.totale_prelievi
        )

    @property
    def differenza_cassa(self):
        """Calcola la differenza tra cassa reale e teorica"""
        if self.conteggio_cassa_reale is not None:
            return self.conteggio_cassa_reale - self.cassa_teorica_finale
        return None

    @property
    def stato_differenza(self):
        """Restituisce lo stato della differenza (ok, mancante, eccedente)"""
        diff = self.differenza_cassa
        if diff is None:
            return 'non_chiusa'
        if abs(diff) < Decimal('0.50'):  # Tolleranza 50 centesimi
            return 'ok'
        elif diff < 0:
            return 'mancante'
        else:
            return 'eccedente'

    @property
    def totale_incassi_giornalieri(self):
        """Totale incassi di tutti i metodi"""
        return (
            self.totale_incassi_contanti +
            self.totale_carte +
            self.totale_bancomat +
            self.totale_bonifici +
            self.totale_altro
        )

    def ricalcola_totali(self):
        """Ricalcola tutti i totali dai movimenti e pagamenti"""
        from apps.ordini.models import Pagamento
        from django.db.models import Sum, Q

        # Pagamenti del giorno
        pagamenti = Pagamento.objects.filter(
            data_pagamento__date=self.data
        )

        # Incassi contanti
        self.totale_incassi_contanti = pagamenti.filter(
            metodo='contanti'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        # Altri metodi
        self.totale_carte = pagamenti.filter(
            metodo='carta'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_bancomat = pagamenti.filter(
            metodo='bancomat'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_bonifici = pagamenti.filter(
            metodo='bonifico'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        # Movimenti cassa
        movimenti = self.movimenti.all()

        self.totale_pagamenti_contanti = movimenti.filter(
            tipo='uscita',
            categoria='pagamento'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_prelievi = movimenti.filter(
            tipo='prelievo'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.totale_versamenti = movimenti.filter(
            tipo='versamento'
        ).aggregate(
            totale=Sum('importo')
        )['totale'] or Decimal('0.00')

        self.save()

    def chiudi(self, conteggio_reale, note='', operatore=None):
        """Chiude la cassa con il conteggio reale"""
        if self.stato == 'chiusa':
            raise ValueError("La cassa è già chiusa")

        self.conteggio_cassa_reale = conteggio_reale
        self.note_chiusura = note
        self.data_ora_chiusura = timezone.now()
        self.operatore_chiusura = operatore
        self.stato = 'chiusa'
        self.save()

    def conferma_chiusura(self):
        """Conferma definitivamente la chiusura (blocco modifiche)"""
        if self.stato != 'chiusa':
            raise ValueError("Devi prima chiudere la cassa")
        self.confermata = True
        self.save()


class MovimentoCassa(models.Model):
    """Traccia i movimenti di cassa non legati a ordini (spese, prelievi, versamenti)"""

    TIPO_CHOICES = [
        ('entrata', 'Entrata'),
        ('uscita', 'Uscita'),
        ('prelievo', 'Prelievo'),
        ('versamento', 'Versamento'),
    ]

    CATEGORIA_CHOICES = [
        ('pagamento', 'Pagamento Fornitore/Spesa'),
        ('prelievo_banca', 'Prelievo per Banca'),
        ('versamento_banca', 'Versamento da Banca'),
        ('fondo_cambio', 'Fondo Cambio'),
        ('altro', 'Altro'),
    ]

    chiusura_cassa = models.ForeignKey(
        ChiusuraCassa,
        on_delete=models.CASCADE,
        related_name='movimenti'
    )

    data_ora = models.DateTimeField(default=timezone.now)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES)

    importo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    causale = models.CharField(max_length=200, help_text="Descrizione del movimento")
    dettagli = models.TextField(blank=True)

    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    # Documenti
    riferimento_documento = models.CharField(
        max_length=100,
        blank=True,
        help_text="Numero fattura, ricevuta, etc."
    )

    creato_il = models.DateTimeField(auto_now_add=True)
    modificato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Movimento Cassa"
        verbose_name_plural = "Movimenti Cassa"
        ordering = ['-data_ora']

    def __str__(self):
        segno = '+' if self.tipo in ['entrata', 'versamento'] else '-'
        return f"{segno}€{self.importo} - {self.causale} ({self.data_ora.strftime('%d/%m/%Y %H:%M')})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Ricalcola i totali della chiusura cassa
        self.chiusura_cassa.ricalcola_totali()


# ---------------------------------------------------------------------------
# Estensione ChiusuraCassa (proprieta aggregate)
# ---------------------------------------------------------------------------

def _num_ordini_giorno(self):
    from apps.ordini.models import Ordine
    return Ordine.objects.filter(data_ora__date=self.data, vendita_prodotti=False).count()


def _num_washcycles_giorno(self):
    """Conta i washcycles venduti (items con servizio 'washcycle' o simili).
    Euristico: item che hanno un servizio il cui nome contiene 'wash' o 'lavaggio'."""
    from apps.ordini.models import ItemOrdine
    return ItemOrdine.objects.filter(
        ordine__data_ora__date=self.data,
        servizio_prodotto__tipo='servizio',
    ).count()


def _totale_incassi_giorno(self):
    """Somma di tutti i metodi di pagamento del giorno (servito)."""
    return self.totale_incassi_giornalieri


ChiusuraCassa.num_ordini_giorno = property(_num_ordini_giorno)
ChiusuraCassa.num_washcycles_giorno = property(_num_washcycles_giorno)


# ---------------------------------------------------------------------------
# Chiusura giornaliera casse automatiche (cambia gettoni, portali)
# ---------------------------------------------------------------------------

class ChiusuraCassaAutomatica(models.Model):
    """Chiusura giornaliera di una cassa automatica (cambia gettoni, portale blu/azzurro)."""
    cassa = models.ForeignKey(
        Cassa, on_delete=models.PROTECT,
        related_name='chiusure_automatiche',
        limit_choices_to={'tipo': 'automatica'},
    )
    data = models.DateField(default=timezone.now)
    data_ora_chiusura = models.DateTimeField(auto_now_add=True)
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='chiusure_automatiche',
    )

    # Incassi
    incasso_totale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Incasso totale',
    )
    incasso_ricarica = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Incasso ricarica',
    )

    # Vendite
    vendita_contante = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Vendita contante',
    )
    vendita_non_contante = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Vendita non contante',
    )

    # Verifica fisica (opzionale)
    resto_erogato_reale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Resto erogato reale (verifica fisica)',
        help_text='Conteggio reale del resto erogato (per verificare con il teorico)',
    )

    # Conteggio fisico dei contanti presenti nella cassa a fine giornata
    contanti_conteggiati = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Contanti conteggiati (fine giornata)',
        help_text='Somma fisica dei contanti presenti nella cassa a fine giornata',
    )

    # WashCycles (solo per portali)
    wash_cycles = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name='WashCycles',
        help_text='Numero di cicli di lavaggio erogati (solo per i portali)',
    )

    note = models.TextField(blank=True)
    confermata = models.BooleanField(default=False)

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Chiusura cassa automatica'
        verbose_name_plural = 'Chiusure casse automatiche'
        ordering = ['-data', 'cassa__ordine']
        unique_together = [('cassa', 'data')]

    def __str__(self):
        return f"{self.cassa} — {self.data.strftime('%d/%m/%Y')}"

    @property
    def incasso_vendita(self):
        """Incasso dalle vendite = incasso totale - incasso ricarica."""
        return self.incasso_totale - self.incasso_ricarica

    @property
    def vendita_totale(self):
        """Vendita totale = contante + non contante."""
        return self.vendita_contante + self.vendita_non_contante

    @property
    def resto_erogato_teorico(self):
        """Resto erogato teorico = incasso vendita - vendita totale."""
        return self.incasso_vendita - self.vendita_totale

    @property
    def differenza(self):
        """Differenza tra resto erogato reale e teorico."""
        return self.resto_erogato_reale - self.resto_erogato_teorico

    @property
    def stato_differenza(self):
        """Stato della differenza (ok, mancante, eccedente)."""
        diff = self.differenza
        if abs(diff) < Decimal('0.50'):
            return 'ok'
        elif diff < 0:
            return 'mancante'
        else:
            return 'eccedente'

    @property
    def contanti_teorici(self):
        """Contanti teorici: vendita contante - resto erogato."""
        if self.cassa.modalita_registratore:
            return self.incasso_totale
        return self.vendita_contante - self.resto_erogato_teorico


# Prezzi dei programmi dei portali a spazzole (WashTec). I programmi
# senza prezzo (P8, P9) valgono 0 e il report li segnala come "senza
# importo": aggiornare qui quando il listino viene definito.
PREZZI_PROGRAMMA_PORTALE = {
    1: Decimal('15.00'), 2: Decimal('12.00'), 3: Decimal('10.00'),
    4: Decimal('8.00'), 5: Decimal('3.00'),
    6: Decimal('8.00'), 7: Decimal('15.00'), 8: Decimal('0.00'),
    9: Decimal('0.00'),
}


class TransazionePortale(models.Model):
    """Singola transazione dei portali a spazzole, estratta da WashTec
    Plus (Report > Dati transazione): l'archivio grezzo da cui il
    report giornata aggrega i lavaggi della finestra di chiusura.

    I due portali hanno lo stesso nome postazione su WashTec e si
    distinguono dalla serie del contatore transazioni (vedi
    services/import_washtec). A = Azzurro e B = Blu: dedotto l'08/10/26
    dalle vendite in contanti degli scontrini delle due casse.
    L'import e' idempotente: (portale, numero) e' univoco.
    """
    PORTALE_CHOICES = [
        ('A', 'Portale Azzurro (A)'),
        ('B', 'Portale Blu (B)'),
    ]
    # ATTENZIONE alle etichette WashTec, che sono invertite rispetto
    # alla realta' operativa: il "Metodo di pagamento" WashTec
    # "Unita' operativa parallela" corrisponde ai CONTANTI, mentre
    # "In contanti" corrisponde all'UNITA' OPERATIVA. L'import deve
    # mappare di conseguenza.
    ORIGINE_CHOICES = [
        ('contanti', 'Contanti'),
        ('unita', 'Unita\' operativa'),
    ]

    portale = models.CharField(max_length=1, choices=PORTALE_CHOICES)
    numero = models.PositiveIntegerField(
        verbose_name='Numero transazione WashTec')
    orario = models.DateTimeField(db_index=True)
    programma = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(9)])
    origine = models.CharField(max_length=10, choices=ORIGINE_CHOICES)
    importato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Transazione portale (WashTec)'
        verbose_name_plural = 'Transazioni portali (WashTec)'
        ordering = ['orario']
        unique_together = [('portale', 'numero')]

    def __str__(self):
        return (f"{self.get_portale_display()} #{self.numero} "
                f"P{self.programma} {self.orario:%d/%m/%Y %H:%M}")


class ChiusuraPortali(models.Model):
    """Finestra di chiusura dei portali per una giornata del report.

    La "giornata" dei portali e' a cavallo di due date (es. report del
    06/10 = dal 05/10 19:00 al 06/10 18:00) e gli orari variano:
    li imposta l'operatore nel report giornata. I lavaggi mostrati
    sono le TransazionePortale con orario nella finestra (estremo
    iniziale escluso, finale incluso).

    periodo_da/periodo_a valgono per il portale Azzurro (A) e per i
    lavaggi del servito; gli scontrini delle due casse pero' vengono
    chiusi in momenti diversi (il Blu spesso 15-20 minuti dopo), quindi
    il Blu (B) puo' avere orari suoi: se vuoti valgono quelli generali.
    """
    data = models.DateField(unique=True)
    periodo_da = models.DateTimeField(verbose_name='Inizio chiusura')
    periodo_a = models.DateTimeField(verbose_name='Fine chiusura')
    periodo_da_blu = models.DateTimeField(
        null=True, blank=True, verbose_name='Inizio chiusura Blu',
        help_text='Solo se diverso dall\'inizio generale')
    periodo_a_blu = models.DateTimeField(
        null=True, blank=True, verbose_name='Fine chiusura Blu',
        help_text='Solo se diversa dalla fine generale')
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='chiusure_portali')
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Chiusura portali'
        verbose_name_plural = 'Chiusure portali'
        ordering = ['-data']

    def __str__(self):
        return (f"Chiusura portali {self.data:%d/%m/%Y} "
                f"({self.periodo_da:%d/%m %H:%M} - {self.periodo_a:%d/%m %H:%M})")

    def finestra(self, portale):
        """(inizio escluso, fine inclusa) della finestra del portale."""
        if portale == 'B':
            return (self.periodo_da_blu or self.periodo_da,
                    self.periodo_a_blu or self.periodo_a)
        return self.periodo_da, self.periodo_a

    @property
    def orari_blu_diversi(self):
        return self.finestra('B') != self.finestra('A')

    def contiene(self, transazione):
        da, a = self.finestra(transazione.portale)
        return da < transazione.orario <= a

    def q_transazioni(self, prefisso=''):
        """Q delle TransazionePortale nella finestra del loro portale
        (prefisso per filtrare da un modello collegato, es. 'transazione__')."""
        q = models.Q(pk__in=[])
        for portale in ('A', 'B'):
            da, a = self.finestra(portale)
            q |= models.Q(**{f'{prefisso}portale': portale,
                             f'{prefisso}orario__gt': da,
                             f'{prefisso}orario__lte': a})
        return q


class AbbinamentoPortale(models.Model):
    """Collega un lavaggio del servito (ItemOrdine con programmi_portale)
    alla transazione portale WashTec, avviata da unita' operativa, che
    lo ha eseguito. Le transazioni 'unita' NON abbinate sono i lavaggi
    pagati direttamente agli operatori.

    Una transazione si abbina una sola volta (OneToOne); un item con
    quantita N accetta fino a N abbinamenti (vincolo nel service).
    """
    transazione = models.OneToOneField(
        TransazionePortale, on_delete=models.CASCADE,
        related_name='abbinamento')
    item = models.ForeignKey(
        'ordini.ItemOrdine', on_delete=models.CASCADE,
        related_name='abbinamenti_portale')
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='abbinamenti_portale')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Abbinamento portale'
        verbose_name_plural = 'Abbinamenti portale'
        ordering = ['transazione__orario']

    def __str__(self):
        return f"{self.transazione} -> item {self.item_id}"


class RettificaResiduo(models.Model):
    """Un lavaggio del residuo (unita' operativa non abbinata al servito)
    che gli operatori NON hanno incassato a listino: omaggio, promo...
    La quadratura conta l'importo indicato qui al posto del listino."""
    MOTIVO_CHOICES = [
        ('omaggio', 'Omaggio'),
        ('promo', 'Promo / sconto'),
        ('altro', 'Altro'),
    ]
    transazione = models.OneToOneField(
        TransazionePortale, on_delete=models.CASCADE, related_name='rettifica_residuo')
    motivo = models.CharField(max_length=10, choices=MOTIVO_CHOICES)
    importo = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Incassato dagli operatori')
    nota = models.CharField(max_length=200, blank=True)
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rettifiche_residuo')
    modificato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Rettifica residuo portali'
        verbose_name_plural = 'Rettifiche residuo portali'

    def __str__(self):
        return f'{self.transazione} {self.get_motivo_display()} {self.importo}'


# ---------------------------------------------------------------------------
# Quadratura giornaliera complessiva (scassettamento)
# ---------------------------------------------------------------------------

class QuadraturaGiornaliera(models.Model):
    """
    Quadratura a fine giornata.
    L'operatore scassetta tutte le casse automatiche + il registratore e conta
    TUTTI i contanti insieme + il totale del lettore carte del servito.
    Il totale viene confrontato con la vendita totale self-service + ordini POS pagati.
    """
    data = models.DateField(unique=True, default=timezone.now)

    contanti_totali = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Contanti totali conteggiati',
        help_text='Somma di tutto il contante scassettato dalle casse automatiche e dal registratore',
    )
    lettore_carte_servito = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Totale lettore carte POS servito',
        help_text='Totale riportato dal terminale POS (lettore carte) del servito',
    )
    fondo_cassa_iniziale = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Fondo cassa iniziale',
        help_text='Contanti gia presenti in cassa all\'inizio della giornata (verra sottratto dal totale reale)',
    )
    note = models.TextField(blank=True)
    operatore = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='quadrature_giornaliere',
    )

    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Quadratura giornaliera'
        verbose_name_plural = 'Quadrature giornaliere'
        ordering = ['-data']

    def __str__(self):
        return f"Quadratura {self.data.strftime('%d/%m/%Y')}"

    @property
    def totale_reale(self):
        """Totale netto rilevato: contanti + carte - fondo cassa iniziale."""
        return self.contanti_totali + self.lettore_carte_servito - self.fondo_cassa_iniziale


class SpesaCassa(models.Model):
    """Costo sostenuto in giornata pagando con i contanti presi dalla
    cassa: nella quadratura si somma al reale, perche' quei soldi
    mancano dal conteggio."""

    CATEGORIA_CHOICES = [
        ('prodotti', 'Prodotti e materiali'),
        ('carburante', 'Carburante'),
        ('manutenzione', 'Manutenzione e riparazioni'),
        ('personale', 'Personale'),
        ('alimentari', 'Bar e alimentari'),
        ('altro', 'Altro'),
    ]

    data = models.DateField(default=timezone.localdate, db_index=True)
    importo = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    descrizione = models.CharField(max_length=200)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, default='altro')
    riferimento = models.CharField(max_length=100, blank=True,
                                   help_text='Numero scontrino, fattura, ricevuta...')
    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='spese_cassa')
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Spesa pagata dalla cassa'
        verbose_name_plural = 'Spese pagate dalla cassa'
        ordering = ['data', 'creato_il']

    def __str__(self):
        return f"{self.data:%d/%m/%Y} {self.descrizione} €{self.importo}"
