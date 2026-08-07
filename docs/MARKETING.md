# Modulo Marketing / CRM

Segmentazione automatica dei clienti, campagne WhatsApp di
riattivazione, richiamo automatico post-lavaggio e misurazione delle
conversioni. Tutto in `/marketing/` (voce navbar visibile allo staff).

---

## 1. Segmentazione

Ogni cliente con almeno un lavaggio completato viene classificato in
uno di 4 segmenti (calcolati al volo a ogni apertura pagina):

| Segmento | Regola |
|---|---|
| **Attivi regolari** | Ultimo lavaggio in linea con la loro frequenza abituale |
| **In rallentamento** | Giorni dall'ultimo lavaggio > frequenza media personale + delta (default 30) |
| **Dormienti** | Nessun lavaggio da più di N giorni (default 120) |
| **One-shot** | Un solo lavaggio in totale, mai tornati (anche se vecchissimo) |

La frequenza media è stimata come `(ultimo - primo) / (n° lavaggi - 1)`.
Le soglie si cambiano da **Marketing → Impostazioni** e hanno effetto
immediato. Ogni segmento è esplorabile (lista con telefono, ultimo
lavaggio, totale, frequenza) ed esportabile in CSV (separatore `;`,
compatibile con Excel italiano).

## 2. Opt-out ("non contattare")

- Flag `blocca_marketing` sul cliente: escluso da OGNI invio
  promozionale (campagne manuali + richiamo automatico). Le notifiche
  di servizio (conferme prenotazione, auto pronta) NON sono toccate.
- **Attivazione automatica**: se il cliente scrive su WhatsApp una
  stop-word (`STOP`, `basta`, `cancellami`, `disiscrivimi`,
  `unsubscribe`, o frasi come "non contattarmi", "non voglio più
  ricevere"), il flag si attiva da solo e il cliente riceve conferma.
  Se la conversazione non ha un cliente anagrafato, nei log compare un
  warning e l'operatore agisce a mano.
- **Attivazione manuale**: bottone Sì/No nella colonna "Contattabile"
  delle liste segmento, oppure dall'admin Django (sezione Marketing
  della scheda cliente).
- `consenso_marketing` (l'opt-in della registrazione online) resta un
  campo separato e non viene modificato dal modulo.

## 3. Campagne manuali

**Marketing → Campagne → Nuova campagna**:

1. Nome, segmento destinatari, **nome del template Meta approvato**,
   parametri body uno per riga. Placeholder disponibili: `{nome}`,
   `{giorni_ultimo_lavaggio}`, `{totale_lavaggi}` (risolti per-cliente
   al momento dell'invio, con dati freschi).
2. **Anteprima**: quanti contattabili, quanti esclusi (e perché),
   esempio di parametri compilati sui primi 3 destinatari, checkbox
   per deselezionare singoli clienti.
3. **Conferma**: gli invii finiscono *in coda* e partono scaglionati.

> I messaggi promozionali partono SEMPRE come template Meta approvati:
> fuori dalla finestra 24h WhatsApp rifiuta il testo libero (errore
> 132047). Creare prima il template su Meta WhatsApp Manager e
> attendere l'approvazione.

### Esclusioni automatiche (in quest'ordine)

1. Telefono mancante o non valido
2. Opt-out attivo
3. Contattato negli ultimi N giorni (default 30, configurabile)
4. Già in coda in un'altra campagna attiva

Il controllo avviene due volte: al lancio e **di nuovo al momento
dell'invio** (un opt-out arrivato dopo il lancio blocca l'invio).
Gli esclusi restano tracciati come "saltato" con il motivo.

### Invio scaglionato

Il cron invia al massimo `max_invii_giorno` messaggi al giorno
(default 40, tutte le campagne sommate) con pausa casuale tra
`intervallo_min` e `intervallo_max` secondi (default 45–180) tra un
messaggio e l'altro. Serve a non far bloccare il numero WhatsApp
Business dai sistemi anti-spam Meta. Una campagna da 200 destinatari
con i default impiega ~5 giorni: è voluto.

Gli invii partono solo nella **fascia oraria** configurata in
Impostazioni (default 09:00–20:00, ammessa anche a cavallo di
mezzanotte): fuori fascia la coda resta ferma e riparte al primo run
del cron dentro l'orario. Il bottone "Invia ora" sul singolo
destinatario ignora la fascia (scelta esplicita dell'operatore).

Una campagna con invii ancora in coda si può **annullare** dal
dettaglio: gli invii in coda diventano "saltato", quelli già inviati
restano.

## 4. Campagne a flusso continuo

Il vecchio "richiamo automatico" hardcoded non esiste piu': al suo
posto qualsiasi campagna puo' diventare **a flusso continuo** con la
spunta nel composer. Il cron, a ogni run:

- rivaluta i segmenti della campagna e accoda i clienti che vi sono
  ENTRATI nel frattempo (stesse esclusioni delle campagne manuali:
  opt-out, telefono, finestra no-ricontatto);
- ritenta gli invii "saltato" per motivi temporanei appena il cliente
  torna contattabile (l'opt-out resta escluso);
- con **Ricontatto ciclico = N giorni**, un cliente gia' contattato
  che dopo N giorni e' di nuovo nel segmento viene ricontattato
  (0 = ogni cliente riceve la campagna al massimo una volta).

Un flusso continuo non si auto-completa mai: si ferma con Pausa
(riprendibile) o Annulla (definitivo). In lista campagne ha il badge
"flusso continuo".

**Richiamo classico dei clienti fermi**: crea un segmento
personalizzato "giorni da ultimo >= 45", poi una campagna su quel
segmento con flusso continuo attivo e ricontatto ciclico es. 120
giorni. Parametri template: `{nome}` e/o `{giorni_ultimo_lavaggio}`
(il numero di variabili del template Meta viene rispettato in
automatico).

## 5. Misurazione

**Marketing → Campagne**: per ogni campagna inviati / falliti /
in coda / conversioni (con tasso %) / fatturato attribuito. In alto,
la card "Rendimento per segmento" cumula i risultati per segmento di
origine, ordinati per tasso di conversione.

- **Conversione** = il cliente ha completato almeno un lavaggio entro
  N giorni dall'invio (default 21, configurabile; la finestra viene
  congelata sulla campagna al lancio).
- **Fatturato attribuito** = somma dei `totale_finale` di tutti gli
  ordini completati nella finestra (2 ritorni = 1 conversione ma 2
  ordini di fatturato).
- Nel dettaglio campagna ogni invio mostra anche lo stato di consegna
  WhatsApp (✓ inviato, ✓✓ recapitato, ✓✓ blu letto) letto dai webhook
  Meta già attivi per l'inbox.

## Setup Railway (cron)

Creare un nuovo **cron service** sul progetto Railway (stesso pattern
del promemoria prenotazioni):

- Schedule: `*/15 * * * *`
- Command: `python manage.py esegui_campagne_marketing`

Test manuale senza inviare nulla:

```bash
python manage.py esegui_campagne_marketing --dry-run
```

## Note GDPR / deliverability

- Base legale suggerita per clienti esistenti: legittimo interesse
  (soft opt-in art. 130 c.4 Codice Privacy) — comunicazioni su servizi
  analoghi a quelli già acquistati, con opt-out facile e gratuito. La
  STOP-word e il bottone "non contattare" implementano l'opt-out.
- Non alzare `max_invii_giorno` oltre ~50 finché il numero non ha
  costruito reputazione: Meta valuta il tasso di block/report.
- Ogni messaggio promozionale resta visibile nell'inbox `/messaggi/`
  del gestionale (stesso storico delle altre comunicazioni).


## Consulente AI (Claude API)

Pagina **Marketing -> Consulente AI**: analizza i dati aggregati del
modulo (KPI, segmenti, rendimento campagne, impostazioni) e produce:

- analisi discorsiva dell andamento + best practice;
- **proposte di segmenti** (un click -> crea il SegmentoPersonalizzato);
- **proposte di campagne** (un click -> composer precompilato: si passa
  comunque da preview e conferma, NULLA parte in automatico);
- **proposte di template WhatsApp**: bozze con testo pronto ("Copia
  testo") da incollare in Meta WhatsApp Manager -> Message templates;
- **promozioni** realistiche (sconti, bundle, gettoni omaggio)
  calibrate su listino prezzi e vendite reali.

Oltre ai dati marketing, l AI legge il listino servizi con i prezzi e
gli aggregati di vendita (fatturato 30/90gg, scontrino medio, top
servizi, fatturato per giorno della settimana, self-service) per
analisi piu profonde.

Garanzie:

- ogni campagna proposta mostra lo stato del template con un badge:
  verde "approvato", giallo "da creare su Meta" (bozza proposta nella
  stessa analisi), grigio "verifica approvazione". L invio reale
  funziona solo con template approvati da Meta;
- al modello vengono inviati SOLO aggregati anonimi: mai nomi,
  telefoni o dati personali dei clienti;
- ogni analisi e salvata con i token consumati (costo tipico: pochi
  centesimi) ed e consultabile dallo storico in alto a destra.

### Setup

1. Creare una API key su console.anthropic.com.
2. Su Railway (servizio web) impostare `ANTHROPIC_API_KEY`.
3. Facoltativo: `ANTHROPIC_MODEL` per cambiare modello (default
   `claude-opus-5`).

Senza chiave la pagina mostra le istruzioni e il resto del modulo
funziona normalmente (i consigli rule-based della dashboard non usano
l AI e sono sempre gratuiti).
