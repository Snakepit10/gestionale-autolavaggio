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

Una campagna con invii ancora in coda si può **annullare** dal
dettaglio: gli invii in coda diventano "saltato", quelli già inviati
restano.

## 4. Richiamo automatico

**Marketing → Impostazioni → Richiamo automatico**:

- Interruttore ON/OFF
- Giorni dopo l'ultimo lavaggio (default 45)
- Nome del template Meta da usare (obbligatorio: senza, il richiamo
  non parte anche con l'interruttore ON)

Ogni giorno il cron trova i clienti il cui *ultimo* lavaggio risale
esattamente a N giorni fa e li accoda alla campagna mensile
"Richiamo automatico YYYY-MM" (tipo *automatica*, visibile e
misurabile in dashboard come le altre). Stesse esclusioni delle
campagne manuali. Parametri template: `{nome}`,
`{giorni_ultimo_lavaggio}` (il template Meta deve avere 2 variabili).

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
