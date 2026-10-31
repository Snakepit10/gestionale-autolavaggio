"""Contabilita' gestionale: piano dei conti, fatture dei fornitori (dalla
PEC o caricate a mano) e, nelle fasi successive, prima nota, conto
economico, flussi di cassa e analitica.

I ricavi non si registrano qui: vengono dalle chiusure di cassa e dagli
ordini (app finanze/ordini). Il costo del personale viene dall'app
personale. Qui entrano i costi dei fornitori e i movimenti di banca."""
import json
from decimal import Decimal, InvalidOperation

from django.db import models

# Centri dell'analitica. Il "comune" e' la struttura: nel direct costing
# del self-service resta fuori, nel full costing del servito si ripartisce.
CENTRI = [
    ('self', 'Self-service'),
    ('servito', 'Servito'),
    ('prodotti', 'Vendita prodotti'),
    ('comune', 'Comune'),
]
NOMI_CENTRI = dict(CENTRI)


def ripartizione_valida(rip):
    """{'self': 60, 'servito': 40} -> stessa cosa in Decimal, oppure
    ValueError. Vuota = tutto sul comune."""
    if not rip:
        return {}
    pulita = {}
    for centro, pct in rip.items():
        if centro not in NOMI_CENTRI:
            raise ValueError(f'centro "{centro}" sconosciuto')
        try:
            pct = Decimal(str(pct).strip().replace(',', '.'))
        except InvalidOperation:
            raise ValueError(f'{NOMI_CENTRI[centro]}: "{pct}" non e\' un numero')
        if pct < 0 or pct > 100:
            raise ValueError(f'{NOMI_CENTRI[centro]}: la percentuale va da 0 a 100')
        if pct:
            pulita[centro] = pct
    if pulita and sum(pulita.values()) != 100:
        raise ValueError(f'le percentuali sommano {sum(pulita.values()):g}, non 100')
    return pulita


def testo_ripartizione(rip):
    if not rip:
        return 'Comune 100%'
    return ' · '.join(f'{nome} {Decimal(str(rip[c])):g}%' for c, nome in CENTRI if rip.get(c))


class VoceConto(models.Model):
    """Voce del piano dei conti gestionale."""
    SEZIONI = [
        ('ricavi', 'Ricavi'),
        ('costi_variabili', 'Costi variabili'),
        ('costi_fissi', 'Costi fissi'),
        ('personale', 'Personale'),
        ('ammortamenti', 'Ammortamenti'),
        ('oneri_finanziari', 'Oneri finanziari'),
        ('imposte', 'Imposte e tasse'),
        ('non_economico', 'Non economico (solo cassa)'),
    ]
    FLUSSI = [
        ('operativo', 'Gestione operativa'),
        ('investimenti', 'Investimenti'),
        ('finanziario', 'Finanziamenti e titolare'),
        ('giroconto', 'Giroconto (escluso)'),
    ]
    codice = models.CharField(max_length=20, unique=True)
    nome = models.CharField(max_length=80)
    sezione = models.CharField(max_length=20, choices=SEZIONI)
    flusso = models.CharField(max_length=15, choices=FLUSSI, default='operativo')
    # {'self': 70, 'servito': 30}: percentuali per centro; vuota = comune
    ripartizione = models.JSONField(default=dict, blank=True)
    ordine = models.PositiveIntegerField(default=0)
    attiva = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Voce del piano dei conti'
        verbose_name_plural = 'Piano dei conti'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome

    @property
    def economica(self):
        return self.sezione != 'non_economico'

    @property
    def testo_ripartizione(self):
        return testo_ripartizione(self.ripartizione)

    @property
    def ripartizione_json(self):
        return json.dumps(self.ripartizione)


class FatturaPassiva(models.Model):
    ORIGINI = [('pec', 'PEC'), ('upload', 'Caricata'), ('manuale', 'Inserita a mano')]
    TIPI = {
        'TD01': 'Fattura', 'TD02': 'Acconto su fattura', 'TD03': 'Acconto su parcella',
        'TD04': 'Nota di credito', 'TD05': 'Nota di debito', 'TD06': 'Parcella',
        'TD24': 'Fattura differita', 'TD25': 'Fattura differita',
    }
    fornitore = models.ForeignKey('magazzino.Fornitore', on_delete=models.PROTECT,
                                  related_name='fatture_passive')
    tipo_documento = models.CharField(max_length=4, default='TD01')
    numero = models.CharField(max_length=40)
    data = models.DateField()
    descrizione = models.CharField(max_length=255, blank=True)
    # Le note di credito hanno importi negativi
    imponibile = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    totale = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    # Mesi di competenza (primo giorno): una bolletta bimestrale si
    # ripartisce in parti uguali sui mesi
    competenza_dal = models.DateField()
    competenza_al = models.DateField()
    origine = models.CharField(max_length=10, choices=ORIGINI, default='upload')
    # P.IVA|tipo|numero|data: la stessa fattura non entra due volte
    identificativo = models.CharField(max_length=150, unique=True)
    file_nome = models.CharField(max_length=200, blank=True)
    # L'XML resta nel database: su Railway il disco si azzera a ogni deploy
    file_xml = models.BinaryField(null=True, blank=True, editable=False)
    classificata = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Fattura fornitore'
        verbose_name_plural = 'Fatture fornitori'
        ordering = ['-data', '-id']

    def __str__(self):
        return f'{self.fornitore} n. {self.numero} del {self.data:%d/%m/%Y}'

    @property
    def nome_tipo(self):
        return self.TIPI.get(self.tipo_documento, self.tipo_documento)

    @property
    def nota_credito(self):
        return self.tipo_documento in ('TD04', 'TD08')

    def aggiorna_classificata(self):
        """Classificata = tutte le righe hanno una voce."""
        classificata = self.righe.exists() and not self.righe.filter(voce__isnull=True).exists()
        if classificata != self.classificata:
            self.classificata = classificata
            self.save(update_fields=['classificata'])


class RigaFatturaPassiva(models.Model):
    fattura = models.ForeignKey(FatturaPassiva, on_delete=models.CASCADE, related_name='righe')
    numero_linea = models.PositiveIntegerField(default=1)
    descrizione = models.TextField(blank=True)
    imponibile = models.DecimalField(max_digits=12, decimal_places=2)
    aliquota_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    voce = models.ForeignKey(VoceConto, on_delete=models.PROTECT, null=True, blank=True,
                             related_name='righe_fatture')
    # Vuota = la ripartizione della voce
    ripartizione = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'Riga fattura fornitore'
        verbose_name_plural = 'Righe fatture fornitori'
        ordering = ['fattura', 'numero_linea', 'id']

    def __str__(self):
        return f'{self.fattura} - riga {self.numero_linea}'

    @property
    def ripartizione_effettiva(self):
        if self.ripartizione:
            return self.ripartizione
        return self.voce.ripartizione if self.voce else {}


class ScadenzaFattura(models.Model):
    MODALITA = {
        'MP01': 'Contanti', 'MP02': 'Assegno', 'MP05': 'Bonifico', 'MP08': 'Carta',
        'MP09': 'RID', 'MP12': 'RIBA', 'MP16': 'Domiciliazione bancaria',
        'MP19': 'SEPA Direct Debit', 'MP21': 'SEPA Direct Debit B2B',
        'MP22': 'Trattenuta su somme gia\' riscosse', 'MP23': 'PagoPA',
    }
    fattura = models.ForeignKey(FatturaPassiva, on_delete=models.CASCADE, related_name='scadenze')
    data = models.DateField(null=True, blank=True)
    importo = models.DecimalField(max_digits=12, decimal_places=2)
    modalita = models.CharField(max_length=4, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    pagata_il = models.DateField(null=True, blank=True)
    # Pagamento trovato in prima nota (un bonifico puo' pagare piu' scadenze)
    movimento = models.ForeignKey('MovimentoFinanziario', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='scadenze')

    class Meta:
        verbose_name = 'Scadenza fattura fornitore'
        verbose_name_plural = 'Scadenze fatture fornitori'
        ordering = ['data', 'id']

    def __str__(self):
        return f'{self.fattura} - {self.importo} al {self.data}'

    @property
    def nome_modalita(self):
        return self.MODALITA.get(self.modalita, self.modalita)


class RegolaFornitore(models.Model):
    """Voce e ripartizione che le fatture nuove di un fornitore prendono
    da sole ("ricorda per questo fornitore")."""
    fornitore = models.OneToOneField('magazzino.Fornitore', on_delete=models.CASCADE,
                                     related_name='regola_contabile')
    voce = models.ForeignKey(VoceConto, on_delete=models.CASCADE)
    ripartizione = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'Regola fornitore'
        verbose_name_plural = 'Regole fornitori'

    def __str__(self):
        return f'{self.fornitore} -> {self.voce}'


class MessaggioPec(models.Model):
    """Messaggio della casella PEC gia' letto: non si rilegge."""
    message_id = models.CharField(max_length=255, unique=True)
    oggetto = models.CharField(max_length=255, blank=True)
    ricevuto_il = models.DateTimeField(null=True, blank=True)
    letto_il = models.DateTimeField(auto_now_add=True)
    fatture = models.PositiveIntegerField(default=0)
    esito = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Messaggio PEC letto'
        verbose_name_plural = 'Messaggi PEC letti'
        ordering = ['-letto_il']

    def __str__(self):
        return self.oggetto or self.message_id


# ---------------------------------------------------------------- prima nota

class ContoFinanziario(models.Model):
    """Conto di banca, carta o cassa contanti. Il saldo parte da
    saldo_iniziale alla data_saldo_iniziale; i movimenti precedenti non
    contano."""
    TIPI = [('banca', 'Banca'), ('cassa', 'Cassa contanti'), ('carta', 'Carta'), ('altro', 'Altro')]
    nome = models.CharField(max_length=60, unique=True)
    tipo = models.CharField(max_length=10, choices=TIPI, default='banca')
    iban = models.CharField(max_length=34, blank=True)
    saldo_iniziale = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    data_saldo_iniziale = models.DateField()
    attivo = models.BooleanField(default=True)
    ordine = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Conto'
        verbose_name_plural = 'Conti'
        ordering = ['ordine', 'nome']

    def __str__(self):
        return self.nome

    def saldo_al(self, giorno):
        """Saldo a fine giornata."""
        somma = (self.movimenti.filter(data__gte=self.data_saldo_iniziale, data__lte=giorno)
                 .aggregate(s=models.Sum('importo'))['s'])
        return self.saldo_iniziale + (somma or Decimal('0'))


class RegolaMovimento(models.Model):
    """"La descrizione contiene X" -> voce. Vale la prima regola (per
    ordine) che corrisponde."""
    SEGNI = [('', 'Entrate e uscite'), ('entrate', 'Solo entrate'), ('uscite', 'Solo uscite')]
    testo = models.CharField(max_length=100)
    segno = models.CharField(max_length=10, choices=SEGNI, blank=True)
    conto = models.ForeignKey(ContoFinanziario, on_delete=models.CASCADE, null=True, blank=True,
                              help_text='Vuoto = tutti i conti')
    voce = models.ForeignKey(VoceConto, on_delete=models.CASCADE)
    ripartizione = models.JSONField(default=dict, blank=True)
    ordine = models.PositiveIntegerField(default=100)

    class Meta:
        verbose_name = 'Regola movimenti'
        verbose_name_plural = 'Regole movimenti'
        ordering = ['ordine', 'id']

    def __str__(self):
        return f'"{self.testo}" -> {self.voce}'

    def corrisponde(self, movimento):
        if self.conto_id and self.conto_id != movimento.conto_id:
            return False
        if self.segno == 'entrate' and movimento.importo < 0 or self.segno == 'uscite' and movimento.importo > 0:
            return False
        return self.testo.lower() in f'{movimento.causale} {movimento.descrizione}'.lower()


class MovimentoFinanziario(models.Model):
    ORIGINI = [('estratto', 'Estratto conto'), ('cassa', 'Dalla cassa'), ('manuale', 'Inserito a mano')]
    conto = models.ForeignKey(ContoFinanziario, on_delete=models.CASCADE, related_name='movimenti')
    data = models.DateField(db_index=True)
    data_valuta = models.DateField(null=True, blank=True)
    # Entrate positive, uscite negative
    importo = models.DecimalField(max_digits=12, decimal_places=2)
    descrizione = models.TextField(blank=True)
    causale = models.CharField(max_length=120, blank=True)
    # Saldo riportato dall'estratto dopo il movimento, se c'e'
    saldo_estratto = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Stessa riga dello stesso estratto -> stessa impronta: non entra due volte
    impronta = models.CharField(max_length=64)
    voce = models.ForeignKey(VoceConto, on_delete=models.PROTECT, null=True, blank=True,
                             related_name='movimenti')
    ripartizione = models.JSONField(default=dict, blank=True)
    regola = models.ForeignKey(RegolaMovimento, on_delete=models.SET_NULL, null=True, blank=True)
    # Giroconto: il movimento opposto sull'altro conto
    controparte = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True)
    origine = models.CharField(max_length=10, choices=ORIGINI, default='estratto')
    note = models.CharField(max_length=255, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimento'
        verbose_name_plural = 'Prima nota'
        ordering = ['-data', '-id']
        unique_together = [('conto', 'impronta')]

    def __str__(self):
        return f'{self.conto} {self.data:%d/%m/%Y} {self.importo}'

    @property
    def ripartizione_effettiva(self):
        if self.ripartizione:
            return self.ripartizione
        return self.voce.ripartizione if self.voce else {}

    def classifica(self, regole=None):
        """Voce dalla prima regola che corrisponde (se non ce l'ha gia')."""
        if self.voce_id:
            return False
        regole = regole if regole is not None else RegolaMovimento.objects.select_related('voce')
        for regola in regole:
            if regola.corrisponde(self):
                self.voce, self.ripartizione, self.regola = regola.voce, regola.ripartizione, regola
                return True
        return False


class Cespite(models.Model):
    """Impianto o attrezzatura: il costo entra nel conto economico come
    ammortamento in quote mensili uguali dal mese di acquisto; i
    pagamenti (anche a rate) restano solo nei flussi di cassa."""
    descrizione = models.CharField(max_length=120)
    data_acquisto = models.DateField()
    valore = models.DecimalField(max_digits=12, decimal_places=2, help_text='Imponibile, IVA esclusa')
    anni = models.PositiveSmallIntegerField(default=10, help_text="Durata dell'ammortamento")
    centro = models.CharField(max_length=10, choices=CENTRI, default='comune')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Cespite'
        verbose_name_plural = 'Cespiti'
        ordering = ['-data_acquisto']

    def __str__(self):
        return self.descrizione

    @property
    def quota_mensile(self):
        return (self.valore / (self.anni * 12)).quantize(Decimal('0.01'))

    def quota(self, mese):
        """Ammortamento del mese (primo giorno): l'ultima quota chiude il
        valore senza resti di arrotondamento."""
        inizio = self.data_acquisto.replace(day=1)
        n = (mese.year - inizio.year) * 12 + mese.month - inizio.month
        mesi = self.anni * 12
        if n < 0 or n >= mesi:
            return Decimal('0')
        if n == mesi - 1:
            return self.valore - self.quota_mensile * (mesi - 1)
        return self.quota_mensile

    def ammortizzato_al(self, mese):
        inizio = self.data_acquisto.replace(day=1)
        n = (mese.year - inizio.year) * 12 + mese.month - inizio.month + 1
        if n <= 0:
            return Decimal('0')
        if n >= self.anni * 12:
            return self.valore
        return self.quota_mensile * n


# ---------------------------------------------------------------- analitica

class RipartizionePersonale(models.Model):
    """Quota del tempo di un dipendente sui centri (es. 85% servito, 15%
    self per pulizia piste e assistenza), valida dal mese indicato.
    Senza ripartizione il dipendente e' tutto sul servito."""
    dipendente = models.ForeignKey('personale.Dipendente', on_delete=models.CASCADE,
                                   related_name='ripartizioni_centri')
    valido_dal = models.DateField()
    ripartizione = models.JSONField(default=dict)

    class Meta:
        verbose_name = 'Ripartizione del personale'
        verbose_name_plural = 'Ripartizioni del personale'
        ordering = ['dipendente', '-valido_dal']
        unique_together = [('dipendente', 'valido_dal')]

    def __str__(self):
        return f'{self.dipendente} dal {self.valido_dal:%m/%Y}: {self.testo}'

    @property
    def testo(self):
        return testo_ripartizione(self.ripartizione)


class ParametriAnalitica(models.Model):
    """Come i costi comuni (quello che resta sul centro Comune) si
    attribuiscono al servito nel full costing. Una sola riga."""
    CRITERI = [('ricavi', 'In proporzione ai ricavi del servito'), ('percentuale', 'Percentuale fissa')]
    criterio_comuni = models.CharField(max_length=15, choices=CRITERI, default='ricavi')
    percentuale_servito = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('50'))

    class Meta:
        verbose_name = "Parametri dell'analitica"
        verbose_name_plural = "Parametri dell'analitica"

    def __str__(self):
        return self.get_criterio_comuni_display()

    @classmethod
    def attuali(cls):
        return cls.objects.first() or cls()

