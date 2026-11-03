# Contabilità

Pagine in **Amministrazione › Contabilità** (permesso "Contabilità", all'inizio solo titolare).

## Fase 1: fatture dei fornitori

### Come entrano le fatture

**Dalla PEC, in automatico**
- Il comando `leggi_pec_fatture` apre la casella in sola lettura e legge i messaggi degli ultimi 30 giorni.
- Importa gli allegati che sono fatture: `.xml`, `.xml.p7m`, anche dentro `postacert.eml` e negli ZIP.
- Ignora ricevute, notifiche e metadati dello SDI (`_MT_`, `_RC_`, …).
- Un messaggio già letto non si rilegge.

**A mano**
- Nella pagina **Fatture fornitori** si caricano XML, p7m o lo ZIP scaricato dal cassetto fiscale.
- La stessa fattura (P.IVA, tipo, numero, data) non entra mai due volte.

**Senza fattura elettronica**
- Si usa **Inserisci a mano**, per esempio per fornitori esteri o ricevute.

### Configurare la PEC su Railway

Le credenziali le imposta il titolare: non vanno mai scritte nel codice.

1. Nel servizio web, e in quello cron del punto 2, aggiungi le variabili:
   - `PEC_IMAP_HOST`: server IMAP del gestore. Per esempio Aruba è `imaps.pec.aruba.it`, Legalmail è `mbox.cert.legalmail.it`.
   - `PEC_IMAP_USER`: indirizzo PEC.
   - `PEC_IMAP_PASSWORD`: password della casella.
   - Facoltative: `PEC_IMAP_PORT` (default `993`) e `PEC_CARTELLA` (default `INBOX`).
2. Crea un **cron service**, come per `esegui_campagne_marketing`:
   - Schedule: `*/15 * * * *`
   - Command: `python manage.py leggi_pec_fatture`
3. Per una prova a mano usa il pulsante **Leggi la PEC adesso** nella pagina Fatture fornitori, oppure:

   ```bash
   python manage.py leggi_pec_fatture --giorni 90
   ```

Le fatture arrivano sulla PEC solo se allo SDI è comunicato l'indirizzo PEC come canale di ricezione. Se invece la registrazione SDI usa il **codice destinatario** del commercialista, le fatture vanno lì. In quel caso:
- si scarica lo ZIP dal cassetto fiscale e lo si carica a mano;
- oppure si chiede al commercialista di inoltrarle alla PEC.

### Classificazione

**Classificare una fattura**
- Ogni riga va assegnata a una **voce** del piano dei conti.
- Il pulsante **Voce per tutte le righe** assegna la stessa voce a tutte insieme.
- Una fattura è "da classificare" finché ha righe senza voce.

**Ricorda per il fornitore**
- Crea una regola: le fatture nuove di quel fornitore prendono da sole voce e ripartizione.

**Regole per riga (fornitori con merce di centri diversi)**
- Nella fattura, il pannello **Regole per riga** dice: "se la riga contiene X, voce Y e ripartizione Z". Si possono mettere più parole separate da virgola: basta che la riga ne contenga una.
- La bacchetta su una riga precompila la regola con le prime parole della descrizione, la voce e la ripartizione della riga.
- Ordine di applicazione:
  1. vince la regola per riga con la parola più lunga trovata (per esempio "shampoo lavaggio" prima di "shampoo");
  2. senza voce nella regola, si usa la voce della regola generale;
  3. le righe senza parole che corrispondono prendono la regola generale del fornitore.
- Valgono per le fatture nuove. Per quelle già caricate c'è **Applica le regole**, su questa fattura o anche sulle altre da classificare dello stesso fornitore: sovrascrive le righe che corrispondono a una regola.
- L'elenco completo è nel Piano dei conti.

**Competenza**
- Indica i mesi in cui il costo entra nel conto economico, divisi in parti uguali.
- Serve per esempio per le bollette bimestrali.

### Piano dei conti e centri

**Sezione della voce**
- Decide dove va la voce nel conto economico:
  - ricavi;
  - costi variabili;
  - costi fissi;
  - personale;
  - ammortamenti;
  - oneri finanziari;
  - imposte.
- Le voci **non economiche** contano solo nei flussi di cassa: giroconti, IVA, rate, prelievi, pagamento fornitori e stipendi.

**Flusso**
- Decide in quale parte del rendiconto di cassa vanno i movimenti: operativo, investimenti o finanziamenti.
- I giroconti sono esclusi.

**Ripartizione**
- Indica le percentuali sui centri Self-service, Servito, Vendita prodotti e Comune.
- Quello che non è assegnato va sul **Comune**: è escluso dal direct costing del self-service e viene ripartito nel full costing del servito.
- Una riga di fattura può avere una ripartizione diversa da quella della voce.

## Fase 2: prima nota

### Conti

- All'inizio ci sono **BAPS**, **MPS** e **Cassa contanti**, con saldo 0 al 1/1/2026.
- Dalla pagina **Conti** si imposta il saldo vero alla data di partenza.
- Il saldo calcolato = saldo iniziale + movimenti da quella data in poi.
- Se l'estratto riporta il saldo (BAPS), la pagina controlla che coincida.

### Import degli estratti: modelli

Il file diventa una tabella, qualunque sia il formato:
- `.xlsx`;
- `.xls` Excel 97-2003, letto con `xlrd`;
- CSV;
- "xls" che in realtà sono pagine HTML.

Un **modello** dice in quale colonna stanno data, valuta, importo (o dare/avere), descrizione, causale e saldo. La pagina **Modelli di estratto** si apre dalla Prima nota.

| Modello | Colonne | Note |
|---|---|---|
| Con intestazione | trovate per nome | |
| MPS - Excel senza intestazione | A data, B valuta, C causale, D descrizione, F importo | La riga finale "Totale" serve a controllare che la somma dei movimenti letti torni. |
| BAPS - Excel senza intestazione, con saldo | A data, B valuta, C "EUR", D importo, E descrizione, F saldo | Si controlla che i saldi siano coerenti riga per riga. |
| BAPS - Excel 97-2003 (.xls) | A data, B valuta, D importo, E descrizione | La F (nota) si ignora. |
| Personalizzati | colonne indicate a mano (lettera o numero) | Per banche o formati nuovi. |

**Riconoscimento del modello**
1. Si usa il modello scelto all'import.
2. Se non c'è, quello fissato sul conto (pagina Conti).
3. Se non c'è nemmeno quello:
   - si cerca una riga di intestazione;
   - altrimenti si prova ogni modello predefinito. Vale quello per cui quasi tutte le righe lette hanno la forma attesa.

**Prova un file**
- Nella pagina dei modelli mostra modello, periodo, entrate, uscite, controlli e prime righe.
- **Non importa niente.**

Reimportare lo stesso periodo, anche con file che si sovrappongono, non crea doppioni. Due movimenti identici nello stesso giorno restano due.

### Classificazione

- **Regole:** "se la causale o la descrizione contiene X, la voce è Y". Si applicano ai movimenti nuovi.
  - Ci sono già quelle per POS in entrata, commissioni, canoni, bolli, interessi, rate e stipendi.
  - Una regola si crea anche dal singolo movimento, con la bacchetta.
- **Voci economiche:** solo per i movimenti **senza fattura**, per esempio commissioni e interessi.
- **Pagamento di una fattura:**
  - si **abbina** alla scadenza e diventa "Pagamento fornitori";
  - così il costo non si conta due volte;
  - la prima nota propone da sola gli abbinamenti: stesso importo, entro 45 giorni, con preferenza per il nome del fornitore nella descrizione;
  - un bonifico può pagare più fatture;
  - se l'importo non coincide, per esempio perché il bonifico ha dentro la commissione, la **differenza** va su una voce economica scelta nella pagina del movimento (di default Spese bancarie). Una differenza positiva, cioè pagato meno delle fatture, è un abbuono e riduce il costo della voce scelta;
  - la prima nota propone anche i bonifici fino a €5 più alti della scadenza, ma solo se la descrizione ha il nome del fornitore e prima vengono gli importi esatti. "Abbina tutti" mette la differenza su Spese bancarie;
  - nel conto economico entra solo la differenza; nei flussi di cassa il movimento si divide fra Pagamento fornitori e la voce della differenza.
- **Giroconto:** per un passaggio fra conti (per esempio il versamento dei contanti in banca). Crea il movimento opposto sull'altro conto, ed entrambi restano fuori dal rendiconto di cassa.

### Cassa contanti

Dalla pagina Conti, **Aggiorna** scrive i movimenti del mese.

| Movimento | Importo | Voce |
|---|---|---|
| Incassi in contanti del giorno | contati − fondo + spese di cassa + ticket di resto | — |
| Spese di cassa | una per spesa | dalla categoria (carburante, manutenzione, …) |
| Ticket di resto pagati | uno per ticket | — |

Si può rigenerare quante volte serve: gli importi si aggiornano e le voci cambiate a mano restano.

## Fase 3: conto economico e flussi di cassa

### Conto economico (per competenza, IVA esclusa)

Si legge per mese o per trimestre, con il confronto con l'anno prima.

**Ricavi:** due basi, che si scelgono con i pulsanti in alto.

| Base | Calcolo | Note |
|---|---|---|
| **Rilevato reale − IVA** (predefinita) | soldi rilevati nelle quadrature (contanti + POS − fondo + spese di cassa + ticket di resto) − IVA sui corrispettivi | IVA = (registratore + vendita self service) − imponibile al 22%, come nel report periodo. I giorni senza quadratura sono segnalati tra i controlli. |
| **Fatturato per canale** | quanto descritto qui sotto | |

**Ricavi**
- Vengono dalla funzione `ricavi_periodo` (in `apps/finanze/services/ricavi.py`), la stessa del report periodo, divisi per 1,22.
- Canali:
  - servito (lavaggi);
  - vendita prodotti;
  - portali;
  - cambia gettoni e piste;
  - monete online.
- Si aggiungono i movimenti classificati su voci di ricavo.

**Costi**
- Righe delle fatture, ripartite sui mesi di competenza. Quelle senza voce vanno in "Fatture da classificare", tra i costi fissi.
- Movimenti di banca e cassa su voci economiche che **non** pagano una fattura.
- Costo aziendale del personale (app personale).
- Ammortamenti dei cespiti.

**Risultati**

| Risultato | Formula |
|---|---|
| Margine di contribuzione | ricavi − costi variabili |
| EBITDA | margine − costi fissi − personale |
| EBIT | EBITDA − ammortamenti |
| Risultato prima delle imposte | EBIT − oneri finanziari |
| Risultato netto | risultato prima delle imposte − imposte |

Da ogni voce si apre il dettaglio dei documenti. In alto c'è l'elenco di quello che manca: fatture e movimenti da classificare, pagamenti fornitori senza fattura abbinata, spese di cassa non riportate, mesi senza costo del personale.

**Pagamenti fornitori senza fattura**
- "Pagamento fornitori" non è una voce economica: il costo entra solo dalla fattura.
- Un'uscita su quella voce senza fattura abbinata quindi non è nel conto economico. L'avviso ne dà numero e totale (IVA compresa) e apre la prima nota filtrata su quei movimenti.
- Per sistemarli:
  - si carica la fattura e si abbina il pagamento;
  - oppure, se la fattura non esiste, si sposta il movimento su una voce di costo (entra IVA compresa, nel mese del pagamento).

### Flussi di cassa (per cassa, IVA compresa)

- Contano solo i movimenti reali dei conti, raggruppati per flusso della voce: gestione operativa, investimenti, finanziamenti e titolare.
- Saldo iniziale + flusso netto = saldo finale.
- I giroconti sono esclusi.

### Cespiti

- Valore senza IVA, data di acquisto, anni e centro.
- L'ammortamento va nel conto economico in quote mensili uguali dal mese di acquisto.
- Le rate e i pagamenti dell'acquisto restano nei flussi di cassa.

## Fase 4: contabilità analitica

Ogni costo del conto economico, fino all'EBIT, si divide sui centri **Self-service**, **Servito**, **Vendita prodotti** e **Comune**.

**Ricavi:** le stesse due basi del conto economico, con gli stessi pulsanti.

| Base | Come si dividono i ricavi sui centri |
|---|---|
| **Rilevato reale − IVA** (predefinita) | Il rilevato non dice da che canale vengono i soldi. Quello di ogni mese si divide fra servito, vendita prodotti, portali e piste in proporzione al loro fatturato dello stesso mese. Le monete online restano fuori, perché non passano dalla cassa. Un mese con rilevato ma senza fatturato va tutto sul servito. |
| **Fatturato per canale** | fatturato di ogni canale / 1,22 |

Con tutte e due le basi il totale del quadro per centro coincide con l'EBIT del conto economico calcolato sulla stessa base.

**Come si divide ogni costo**
- **Fatture e movimenti:** con la ripartizione della riga o del movimento, se c'è, altrimenti con quella della voce. Senza nessuna delle due il costo va sul Comune.
- **Personale:** costo e ore di ogni dipendente si dividono con la sua ripartizione. Si imposta in Analitica › Impostazioni, con lo storico "valida dal mese". Senza ripartizione il dipendente conta 100% servito.
- **Ammortamenti:** sul centro del cespite.

**Self-service: direct costing**

| Voce | Calcolo |
|---|---|
| Margine I | ricavi self (portali, piste, monete online) − costi variabili |
| Margine II | margine I − costi fissi specifici del self |

- I costi comuni non si ripartiscono sul self.
- Valori per ciclo dei portali: ricavo, costo variabile, cicli di pareggio.

**Servito: full costing**

| Voce | Calcolo |
|---|---|
| Margine diretto | ricavi − costi diretti |
| Risultato pieno | margine diretto − quota dei costi comuni |

- La quota dei costi comuni è in proporzione ai ricavi del servito, oppure una percentuale fissa (si sceglie nelle impostazioni).
- Si calcolano anche:
  - costo pieno per lavaggio;
  - costo pieno per ora lavorata;
  - costo pieno per servizio (costo orario × durata dal catalogo), confrontato col ricavo netto medio.

**Vendita prodotti**
- Margine = ricavi − costi assegnati al centro prodotti (per esempio "Prodotti per la rivendita").

**Quadro per centro**
- Il totale coincide con l'EBIT del conto economico.
