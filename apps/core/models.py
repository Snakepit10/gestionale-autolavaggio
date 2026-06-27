from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal


class Categoria(models.Model):
    nome = models.CharField(max_length=100)
    descrizione = models.TextField(blank=True)
    ordine_visualizzazione = models.IntegerField(default=0)
    attiva = models.BooleanField(default=True)
    solo_pubblico = models.BooleanField(
        default=False,
        verbose_name='Solo prenotazione online',
        help_text="Se attivo, la categoria (e tutti i suoi item) viene "
                  "mostrata SOLO nel catalogo della prenotazione online "
                  "(/app/servizi/) e nascosta all'operatore in cassa "
                  "(/ordini/cassa/). Utile per gestire cataloghi diversi "
                  "tra cliente self-service e operatore al banco.",
    )
    selezione_singola = models.BooleanField(
        default=False,
        verbose_name='Selezione singola nel wizard',
        help_text="Se attivo, nel wizard di prenotazione online il cliente "
                  "puo' scegliere UN solo servizio in questa categoria "
                  "(comportamento radio button). Se disattivo, puo' "
                  "selezionarne piu' di uno (comportamento checkbox, "
                  "default). Usa singola per scelte alternative tipo "
                  "'Esterno base / Esterno completo / Esterno premium'.",
    )

    class Meta:
        ordering = ['ordine_visualizzazione', 'nome']
        verbose_name_plural = "Categorie"

    def __str__(self):
        return self.nome


class Postazione(models.Model):
    nome = models.CharField(max_length=100)
    descrizione = models.TextField(blank=True)
    attiva = models.BooleanField(default=True)
    ordine_visualizzazione = models.IntegerField(default=0)
    
    # Configurazione stampante
    stampante_comande = models.ForeignKey(
        'StampanteRete', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL,
        related_name='postazioni_comande'
    )
    
    class Meta:
        ordering = ['ordine_visualizzazione', 'nome']
        verbose_name_plural = "Postazioni"
    
    def __str__(self):
        return self.nome
    
    def get_ordini_in_coda(self):
        from apps.ordini.models import ItemOrdine
        
        return ItemOrdine.objects.filter(
            postazione_assegnata=self,
            stato__in=['in_attesa', 'in_lavorazione'],
            ordine__stato__in=['in_attesa', 'in_lavorazione']  # Escludi anche ordini completati/annullati
        ).select_related('ordine', 'servizio_prodotto').order_by('ordine__numero_progressivo')
    
    def get_tempo_medio_servizio(self, servizio):
        from apps.ordini.models import ItemOrdine
        from django.db.models import Avg
        
        try:
            tempo_medio = ItemOrdine.objects.filter(
                postazione_assegnata=self,
                servizio_prodotto=servizio,
                stato='completato',
                fine_lavorazione__isnull=False,
                inizio_lavorazione__isnull=False
            ).extra(
                select={'durata': 'EXTRACT(EPOCH FROM (fine_lavorazione - inizio_lavorazione))/60'}
            ).aggregate(media=Avg('durata'))['media']
            
            return tempo_medio or servizio.durata_minuti
        except Exception:
            return servizio.durata_minuti


class ServizioProdotto(models.Model):
    TIPO_CHOICES = [
        ('servizio', 'Servizio'),
        ('prodotto', 'Prodotto'),
    ]
    
    titolo = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='servizio')
    prezzo = models.DecimalField(max_digits=10, decimal_places=2)
    # Categoria primaria: usata per ordinamento e raggruppamento di default.
    # Mantenuta come FK per non rompere il codice esistente che fa
    # s.categoria.nome ovunque. La presenza di un singolo "principale"
    # serve anche per scegliere un ordine canonico nelle pagine che
    # iterano per categoria (es. /catalogo/).
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name='servizi_primari')
    # Categorie aggiuntive: l'item compare anche in queste sezioni del
    # catalogo (cassa + prenotazione online). Es. "Lavaggio esterno" che
    # va sia in "Lavaggi base" (primaria) sia in "Promozioni" (aggiuntiva).
    # Niente cambio per il flusso scorte / postazioni (gestiscono il
    # singolo ServizioProdotto, non le sue categorie).
    categorie_aggiuntive = models.ManyToManyField(
        Categoria, blank=True, related_name='servizi_aggiuntivi',
        help_text="Categorie extra in cui mostrare l'item, oltre alla "
                  "categoria principale. Lascia vuoto se l'item appartiene "
                  "a una sola categoria.",
    )
    descrizione = models.TextField()
    
    # Campi per servizi
    durata_minuti = models.IntegerField(default=30, help_text="Durata stimata in minuti (solo per servizi)")
    postazioni = models.ManyToManyField(Postazione, blank=True, help_text="Postazioni che possono erogare questo servizio")
    
    # Campi per prodotti
    quantita_disponibile = models.IntegerField(default=-1, help_text="-1 = illimitata (per servizi)")
    quantita_minima_alert = models.IntegerField(default=5, help_text="Soglia per alert scorte basse")
    codice_prodotto = models.CharField(max_length=50, blank=True, help_text="SKU/codice interno")
    
    attivo = models.BooleanField(default=True)
    is_supplemento = models.BooleanField(
        default=False,
        verbose_name='Supplemento operatore',
        help_text='Se attivo, disponibile come supplemento dalla dashboard operatore (es. sporco eccessivo)',
    )
    mostra_pubblico = models.BooleanField(
        default=False,
        verbose_name='Mostra al pubblico (lato cliente)',
        help_text='Se attivo, il servizio appare nel catalogo prenotabile dei clienti (/app/servizi/)',
    )
    # Upselling lato prenotazione online: questi 3 campi governano la
    # sezione "Aggiungi extra" nello step di riepilogo del wizard cliente.
    # Indipendenti da mostra_pubblico: un item puo' apparire SOLO nell'upsell
    # (es. profumatore non e' un "lavaggio prenotabile" nello step 1) o in
    # ENTRAMBE le aree (es. aspirazione interni: scelta principale + upsell).
    proponi_in_upsell = models.BooleanField(
        default=False,
        verbose_name='Proponi in upsell',
        help_text="Se attivo, l'item compare nella sezione 'Aggiungi extra' "
                  "del riepilogo prenotazione online.",
    )
    ordine_upsell = models.PositiveSmallIntegerField(
        default=0,
        help_text="Ordinamento nella sezione upsell (0 = primo). "
                  "A parita' di ordine fallback su titolo.",
    )
    upsell_per = models.ManyToManyField(
        'self', symmetrical=False, blank=True,
        related_name='upsell_suggeriti',
        limit_choices_to={'tipo': 'servizio'},
        help_text="Servizi base per cui questo item va proposto come upsell. "
                  "Se vuoto, l'upsell e' universale (mostrato sempre). "
                  "Se valorizzato, mostrato solo quando il cliente ha "
                  "selezionato almeno uno dei servizi indicati.",
    )
    ordine_visualizzazione = models.PositiveSmallIntegerField(
        default=0,
        help_text="Ordinamento dell'item dentro la sua categoria (0 = primo, "
                  "poi 1, 2, ...). A parita' di ordine, fallback su titolo "
                  "alfabetico. L'ordine vale anche per le categorie aggiuntive.",
    )
    gruppo = models.CharField(
        max_length=100, blank=True, default='',
        help_text="Sotto-sezione opzionale dentro lo step del wizard. Item "
                  "con lo stesso gruppo vengono raccolti sotto un'intestazione "
                  "comune (es. 'Pelle' / 'Tessuto' dentro 'Trattamento sedili'). "
                  "Lascia vuoto per item senza sotto-sezione. I gruppi sono "
                  "ordinati alfabeticamente; per controllare l'ordine usa un "
                  "prefisso numerico (es. '1. Pelle', '2. Tessuto').",
    )
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['categoria__ordine_visualizzazione', 'titolo']
        verbose_name_plural = "Servizi e Prodotti"
    
    def __str__(self):
        return f"{self.titolo} ({self.get_tipo_display()})"
    
    @property
    def scorta_bassa(self):
        if self.tipo == 'prodotto' and self.quantita_disponibile > 0:
            return self.quantita_disponibile <= self.quantita_minima_alert
        return False
    
    @property
    def disponibile(self):
        if self.tipo == 'servizio':
            return self.attivo
        return self.attivo and (self.quantita_disponibile == -1 or self.quantita_disponibile > 0)

    @property
    def tutte_le_categorie(self):
        """Lista (no QuerySet) di tutte le categorie a cui l'item appartiene:
        la primaria + le aggiuntive, deduplicate, in ordine. Utile per i
        template che devono renderizzare badge "categorie".
        """
        out = [self.categoria]
        for c in self.categorie_aggiuntive.all():
            if c.pk != self.categoria_id:
                out.append(c)
        return out


class Sconto(models.Model):
    TIPO_CHOICES = [
        ('percentuale', 'Percentuale'),
        ('importo', 'Importo Fisso'),
    ]
    
    titolo = models.CharField(max_length=100)
    tipo_sconto = models.CharField(max_length=20, choices=TIPO_CHOICES)
    valore = models.DecimalField(max_digits=10, decimal_places=2)
    attivo = models.BooleanField(default=True)
    
    class Meta:
        verbose_name_plural = "Sconti"
    
    def __str__(self):
        if self.tipo_sconto == 'percentuale':
            return f"{self.titolo} ({self.valore}%)"
        return f"{self.titolo} (€{self.valore})"
    
    def calcola_sconto(self, importo):
        if self.tipo_sconto == 'percentuale':
            return importo * (self.valore / 100)
        return min(self.valore, importo)


class StampanteRete(models.Model):
    TIPO_CHOICES = [
        ('scontrino', 'Stampante Scontrini'),
        ('comanda', 'Stampante Comande'),
        ('report', 'Stampante Report'),
    ]
    
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    indirizzo_ip = models.GenericIPAddressField()
    porta = models.IntegerField(default=9100)
    modello = models.CharField(max_length=100)
    larghezza_carta = models.IntegerField(default=80, help_text="Larghezza carta in mm")
    attiva = models.BooleanField(default=True)
    predefinita = models.BooleanField(default=False)
    
    class Meta:
        unique_together = [['tipo', 'predefinita']]
        verbose_name_plural = "Stampanti di Rete"
    
    def __str__(self):
        return f"{self.nome} ({self.get_tipo_display()})"
    
    def save(self, *args, **kwargs):
        if self.predefinita:
            # Assicura che solo una stampante per tipo sia predefinita
            StampanteRete.objects.filter(
                tipo=self.tipo, 
                predefinita=True
            ).exclude(pk=self.pk).update(predefinita=False)
        super().save(*args, **kwargs)


class MovimentoScorte(models.Model):
    TIPO_MOVIMENTO = [
        ('carico', 'Carico'),
        ('scarico', 'Scarico'),
        ('rettifica', 'Rettifica Inventario'),
    ]
    
    prodotto = models.ForeignKey(ServizioProdotto, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_MOVIMENTO)
    quantita = models.IntegerField(help_text="Positivo per carico, negativo per scarico")
    quantita_prima = models.IntegerField()
    quantita_dopo = models.IntegerField()
    riferimento_ordine = models.ForeignKey(
        'ordini.Ordine', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL
    )
    nota = models.TextField(blank=True)
    operatore = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    data_movimento = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-data_movimento']
        verbose_name_plural = "Movimenti Scorte"
    
    def __str__(self):
        return f"{self.prodotto.titolo} - {self.get_tipo_display()} ({self.quantita})"