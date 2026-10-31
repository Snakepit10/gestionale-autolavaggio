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

### Import degli estratti

Il formato si riconosce da solo:
- **Excel o CSV con intestazione:** le colonne si trovano per nome (data, valuta, importo oppure dare/avere, descrizione, causale, saldo).
- **BAPS senza intestazione:** colonne data, valuta, EUR, importo, descrizione, saldo.
- **"xls" che sono pagine HTML.**
- Gli `.xls` del vecchio formato Excel vanno salvati come `.xlsx`.

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
  - un bonifico può pagare più fatture.
- **Giroconto:** per un passaggio fra conti (per esempio il versamento dei contanti in banca). Crea il movimento opposto sull'altro conto, ed entrambi restano fuori dal rendiconto di cassa.

### Cassa contanti

Dalla pagina Conti, **Aggiorna** scrive i movimenti del mese.

| Movimento | Importo | Voce |
|---|---|---|
| Incassi in contanti del giorno | contati − fondo + spese di cassa + ticket di resto | — |
| Spese di cassa | una per spesa | dalla categoria (carburante, manutenzione, …) |
| Ticket di resto pagati | uno per ticket | — |

Si può rigenerare quante volte serve: gli importi si aggiornano e le voci cambiate a mano restano.

## Fasi successive

- **Fase 3: conto economico e flussi di cassa.** Più i cespiti e gli ammortamenti.
- **Fase 4: contabilità analitica.** Direct costing self-service, full costing servito e % del personale per centro.
