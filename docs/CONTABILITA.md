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
- Le fatture **emesse** dall'autolavaggio (cedente con la nostra P.IVA, `PARTITA_IVA_AZIENDA`) si scartano, sia dal caricamento sia dalla PEC. Se ne erano entrate prima del blocco, la pagina le segnala con il pulsante **Elimina queste fatture**: restano solo quelle già abbinate a un pagamento. Il fornitore "noi stessi" si cancella se non è usato altrove.
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
- Un estratto non entra se sembra di un altro conto: ha il formato di un'altra banca (il conto ricorda la sua dal primo file riconosciuto) o almeno 5 movimenti, e metà del file, sono già su un altro conto. Se il file è davvero di quel conto si sceglie il modello a mano. Per un estratto BAPS finito su MPS la pagina Conti ha il pulsante **Correggi i movimenti**: i doppioni si tolgono, gli altri si spostano, voci e abbinamenti restano; poi va aggiornata la cassa.

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
  - nella pagina del movimento le scadenze aperte sono in quest'ordine: prima quelle con l'importo esatto, poi quelle dei fornitori nominati nella descrizione (con un'etichetta), poi le più vicine come importo. Sono 15; con la ricerca per fornitore o numero di fattura arrivano fino a 50;
  - **pagamento parziale**: se il movimento paga meno delle scadenze scelte, nella pagina del movimento si sceglie come è stato pagato il resto:
    - *resta da pagare*: l'ultima scadenza si divide e il resto resta aperto, da abbinare poi a un altro movimento (un'altra banca, una spesa di cassa);
    - *pagato in contanti*: oltre alla divisione, crea l'uscita su Cassa contanti, nel giorno indicato, già abbinata al resto. Se gli operatori l'hanno già registrata come spesa di cassa, va scelto "resta da pagare" e abbinata quella, altrimenti l'uscita conta due volte;
    - *sconto o abbuono*: la differenza va su una voce, come sotto.

    Togliendo gli abbinamenti le parti si riuniscono, e l'uscita di cassa creata in automatico si cancella;
  - se l'importo non coincide, per esempio perché il bonifico ha dentro la commissione, la **differenza** va su una voce economica scelta nella pagina del movimento (di default Spese bancarie). Una differenza positiva, cioè pagato meno delle fatture, è un abbuono e riduce il costo della voce scelta;
  - la prima nota propone anche i bonifici fino a €5 più alti della scadenza, ma solo se la descrizione ha il nome del fornitore e prima vengono gli importi esatti. "Abbina tutti" mette la differenza su Spese bancarie;
  - nel conto economico entra solo la differenza; nei flussi di cassa il movimento si divide fra Pagamento fornitori e la voce della differenza.
- **Giroconto:** per un passaggio fra conti (per esempio il versamento dei contanti in banca). Crea il movimento opposto sull'altro conto, ed entrambi restano fuori dal rendiconto di cassa.

Le proposte di abbinamento comprendono anche le uscite con il nome del fornitore nella descrizione, entro 240 giorni: possono pagare più fatture insieme e possono essere già su una voce di costo. In quel caso il costo contava due volte, con la fattura e col pagamento: abbinando, resta solo in fattura. Le spese bancarie restano fuori.

I nomi dei fornitori di 3 lettere (ABM, KSM) valgono solo come parola intera.

Nella pagina **Cespiti** le **proposte** elencano tutto quello che sta sulla voce "Acquisto impianti e attrezzature" e non è ancora un cespite: le fatture con l'imponibile delle righe su quella voce e i pagamenti senza fattura con l'importo pagato. Si spuntano le righe dello stesso acquisto (acconti e saldo) per farne un solo cespite, oppure si tolgono se il bene c'è già. Eliminando il cespite, le sue proposte tornano.

Nello **Scadenziario** si spuntano più scadenze e la barra in fondo le segna *pagate in contanti* (una uscita abbinata sulla Cassa contanti per ognuna; togliendo l'abbinamento l'uscita sparisce) o *già pagate, nessun movimento*. Senza data vale quella della scadenza.

### Cassa contanti

Dalla pagina Conti, **Aggiorna** scrive i movimenti del mese.

| Movimento | Importo | Voce |
|---|---|---|
| Incassi in contanti del giorno | contati − fondo + spese di cassa + ticket di resto, divisi in tre righe | vedi sotto |
| Spese di cassa | una per spesa | dalla categoria (carburante, manutenzione, …) |
| Ticket di resto pagati | uno per ticket | — |
| Versamenti in banca | uscita per ogni entrata di un altro conto sulla voce Giroconto, con "vers" nella descrizione e senza un giroconto fatto a mano | Giroconto |
| Carte contate nei contanti (se impostato "carte dentro i contanti fino al") | un'uscita al mese pari agli accrediti POS arrivati in banca fino a quella data: le quadrature senza il campo POS avevano le carte nei contanti | Incassi |
| Stipendi in contanti (se impostato) | un'uscita al mese: per ogni dipendente il "pagato" del Costo personale o, se manca, il totale da pagare (fuori busta + bonus) | Stipendi |

Si può rigenerare quante volte serve: gli importi si aggiornano e le voci cambiate a mano restano.

**Incassi in contanti per fonte.** I contanti si contano tutti insieme, quindi la divisione è una stima fatta con gli scontrini:
- *dal servito*: il totale del registratore meno il POS del servito della quadratura;
- *dalle casse automatiche*: la vendita totale delle chiusure del cambia gettoni e dei portali (contanti e carte, senza ricarica e resto erogato) meno il POS delle casse automatiche della quadratura;
- *senza scontrino*: il resto, cioè lavaggi pagati agli operatori, lavaggi extra, ricariche, resti e differenze di cassa. Può essere negativo quando in cassa c'è meno di quanto dicono gli scontrini.

Fino al 27/10/2026 il POS del servito e delle casse automatiche era scritto nelle banconote e i due campi POS della quadratura sono a zero: le carte le toglie l'uscita mensile "carte contate nei contanti", pari agli accrediti POS in banca, uguale nelle due versioni. Dal 28/10 si tolgono i POS scritti in quadratura. Il fondo cassa si toglie prima di scrivere le banconote, quindi di solito il campo è a zero. Spese di cassa e ticket di resto non si tolgono dagli incassi: restano uscite separate, con la loro voce. Ogni quadratura comprende anche gli scontrini dei giorni senza quadratura subito prima: quei soldi si contano il giorno dopo. Nei flussi di cassa le tre fonti sono righe separate.

**Conta dei contanti.** Il saldo calcolato non vede i prelievi del titolare, né gli stipendi o le spese pagati in contanti e non registrati. Dalla pagina Conti si registrano i contanti contati a fine giornata (cassa, cassaforte, fondo):
- la differenza con il saldo calcolato diventa un movimento da classificare in prima nota, sulla voce giusta: prelievi del titolare, stipendi, fornitori…;
- la conta resta come controllo del saldo ("quadra al …");
- si ricalcola quando si aggiorna la cassa, se nel frattempo sono entrati versamenti o spese di giorni precedenti.

Senza una conta, Indici e KPI segnala che la liquidità della cassa è solo calcolata.

## Fase 3: conto economico e flussi di cassa

### Conto economico (per competenza, IVA esclusa)

Si legge per mese o per trimestre, con il confronto con l'anno prima.

**Ricavi:** tre basi, che si scelgono con i pulsanti in alto.

| Base | Calcolo | Note |
|---|---|---|
| **Rilevato reale − IVA** (predefinita) | soldi rilevati nelle quadrature (contanti + POS − fondo + spese di cassa + ticket di resto), divisi per canale | **Servito** e **vendita prodotti** = il loro fatturato (IVA compresa) meno l'IVA del registratore fiscale, divisa fra i due in proporzione al fatturato. **Self-service** = il resto del rilevato meno l'IVA dei corrispettivi del self (portali e piste). Il totale resta rilevato − IVA (registratore + self, al 22%). I giorni senza quadratura sono segnalati tra i controlli. |
| **Fatturato per canale** | quanto descritto qui sotto | |
| **Scontrini fiscali** | il punto di vista fiscale: i corrispettivi, IVA esclusa | **Servito** e **vendita prodotti** = il totale del registratore fiscale / 1,22, diviso fra i due in proporzione agli ordini. **Portali** e **cambia gettoni** = le chiusure di cassa / 1,22, come nel fatturato per canale; **monete online** = il loro fatturato / 1,22. Sono i ricavi dei flussi di cassa "dagli scontrini", senza IVA: il servito ordinato ma non battuto al registratore resta fuori. |

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
- Due versioni dei contanti incassati, come le due basi dei ricavi del conto economico:
  - *dalla quadratura reale* (predefinita): i contanti contati, cioè i movimenti della cassa;
  - *dagli scontrini*: al posto degli incassi della cassa, il registratore del servito meno il suo POS e la vendita totale delle casse automatiche meno il loro POS. Spese, ticket, versamenti e conte della cassa restano, e anche i saldi della cassa si spostano della differenza.
- Il POS entra dalla banca ed è uguale nelle due versioni. Sotto il rendiconto c'è la differenza dell'anno fra contanti contati e contanti degli scontrini.

### Cespiti

- Valore senza IVA, data di acquisto, anni e centro.
- L'ammortamento va nel conto economico in quote mensili uguali dal mese di acquisto.
- Le rate e i pagamenti dell'acquisto restano nei flussi di cassa.

## Fase 4: contabilità analitica

Ogni costo del conto economico, fino all'EBIT, si divide sui centri **Self-service**, **Servito**, **Vendita prodotti** e **Comune**.

**Ricavi:** le stesse tre basi del conto economico, con gli stessi pulsanti.

| Base | Come si dividono i ricavi sui centri |
|---|---|
| **Rilevato reale − IVA** (predefinita) | Come nel conto economico, mese per mese. Servito e vendita prodotti: fatturato meno l'IVA del registratore. Self-service: il resto del rilevato meno l'IVA del self, diviso fra portali e piste in proporzione al loro fatturato (tutto ai portali se nel mese non c'è fatturato self). Le monete online restano fuori, perché non passano dalla cassa. |
| **Fatturato per canale** | fatturato di ogni canale / 1,22 |
| **Scontrini fiscali** | servito e vendita prodotti: registratore fiscale / 1,22; self-service: chiusure di cassa e monete online / 1,22 |

Con tutte le basi il totale del quadro per centro coincide con l'EBIT del conto economico calcolato sulla stessa base.

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

**Rientro degli investimenti**
- Usa i cespiti registrati per centro, più il totale aziendale. Il flusso del periodo è riportato a un anno, contando solo i mesi già iniziati.
- **Prima delle imposte:** risultato del centro + i suoi ammortamenti, che non sono uscite di cassa. Per i centri non contano i costi comuni; in totale è l'EBITDA.
- **Dopo imposte e rate:**
  - si tolgono gli oneri finanziari e le rate dei finanziamenti (uscite su "Rimborso finanziamenti"), divisi sui centri in proporzione al valore dei cespiti;
  - si tolgono le imposte, divise in proporzione al flusso positivo dei centri.
- **Anni di rientro** = valore dei cespiti / flusso annuo. Si vede anche il rendimento annuo in %.
- **Data stimata di rientro:** parte dalla data media di acquisto, pesata sul valore, e suppone lo stesso flusso anche negli anni passati.

**Quadro per centro**
- Il totale coincide con l'EBIT del conto economico.

## Fase 5: indici e KPI

Pagina **Indici e KPI**. Gli indici usano i mesi chiusi dell'anno da quando parte la contabilità (il primo saldo iniziale dei conti), riportati a 12 mesi. I ricavi seguono la stessa base del conto economico (rilevato reale − IVA, fatturato per canale o scontrini fiscali). Ogni indice ha la formula e un colore: verde in linea con la prassi bancaria per piccole imprese, giallo da tenere d'occhio, rosso critico.

**Indebitamento massimo** (vale il più prudente dei due metodi)

| Metodo | Calcolo |
|---|---|
| Capacità di rimborso (DSCR) | flusso al servizio del debito = EBITDA − imposte − investimenti di mantenimento; rata annua sostenibile = flusso / DSCR minimo; meno le rate già in corso = rata disponibile; il nuovo debito è il valore attuale della rata disponibile al tasso e per la durata di un nuovo finanziamento |
| Multiplo dell'EBITDA | PFN massima = multiplo × EBITDA; meno la PFN attuale = spazio per nuovo debito |

- **Nuovo finanziamento ottenibile** = il minore dei due spazi; c'è anche la tabella per 3, 5, 7 e 10 anni.
- **Indebitamento massimo sostenibile** = debito di oggi + nuovo ottenibile. Senza spazio è il massimo sostenibile, e se il debito di oggi lo supera la pagina lo segnala.
- Le imposte sono quelle registrate nel periodo; se non ce ne sono si stimano con un'aliquota sull'utile ante imposte.
- Le rate in corso vengono dal piano dei finanziamenti registrati (prossimi 12 mesi). Senza finanziamenti si stimano dalle uscite su "Finanziamenti" degli **ultimi 3 mesi** coperti dagli estratti, su base annua, più gli interessi del conto economico: un debito già finito non pesa sul futuro. Un acquisto pagato a rate al fornitore va sulla voce Investimenti, non Finanziamenti. La liquidità è la somma dei conti attivi, mostrata conto per conto.

**Ipotesi** (pulsante Ipotesi): PFN/EBITDA massimo (3), DSCR minimo (1,25), tasso (6%) e durata (5 anni) del nuovo debito, investimenti di mantenimento in % degli ammortamenti (50%), aliquota imposte stimata (28%), multiplo di valutazione EV/EBITDA (4).

**Indici**
- **Redditività:** crescita dei ricavi sullo stesso periodo dell'anno prima, margine di contribuzione %, EBITDA margin, ROS, utile netto sui ricavi, ROI sugli impianti (EBIT / valore netto dei cespiti), rotazione degli impianti.
- **Struttura dei costi e pareggio:** incidenza di personale e costi fissi; pareggio operativo (EBITDA = 0), economico (utile ante imposte = 0) e di cassa (rate e imposte comprese), annui e al mese; margine di sicurezza; leva operativa.
- **Debito:** PFN (debiti finanziari − liquidità dei conti), PFN/EBITDA, DSCR anche con ricavi −10%, copertura degli interessi, oneri finanziari sui ricavi. DSCR sotto 1 è il segnale d'allerta del Codice della crisi d'impresa.
- **Liquidità:** saldo dei conti, giorni di cassa, liquidità sugli impegni a breve (fornitori da pagare + rate a 12 mesi), conversione dell'EBITDA in cassa e free cash flow (servono gli estratti conto del periodo), giorni di pagamento dei fornitori, fornitori scaduti.
- **KPI operativi:** ricavo al giorno, lavaggi del servito al giorno, ricavo e costo pieno per lavaggio e per ora, cicli self al giorno e ricavo per ciclo, ricavi per euro di personale.
- **Valutazione indicativa:** EBITDA × multiplo − PFN. È un ordine di grandezza, non una perizia.

Non si calcolano ROE e indici patrimoniali: patrimonio netto, crediti e magazzino non sono nel gestionale.

### Finanziamenti

Pagina **Finanziamenti**: mutui, prestiti, leasing e fidi in corso, con importo, tasso annuo, numero di rate mensili e data della prima rata.
- Il piano è alla francese. Se la rata della banca è diversa, la si scrive e il piano si adegua; l'ultima rata chiude il debito.
- Un fido o un anticipo a revoca (0 rate) resta uguale e paga solo gli interessi.
- Il debito conta dal mese prima della prima rata.
- Servono a PFN, DSCR e indebitamento massimo. I pagamenti veri restano in prima nota, sulla voce Finanziamenti.
