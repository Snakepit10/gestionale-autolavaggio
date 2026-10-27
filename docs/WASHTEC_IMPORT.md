# Import automatico WashTec Plus

Le transazioni dei portali a spazzole (Report > Dati transazione di
WashTec Plus) arrivano nel gestionale da sole: un servizio worker apre
WashTec Plus con un browser headless (Playwright), fa il login e legge la
tabella con la stessa estrazione del preferito
(`static/js/washtec_estrazione.js`). WashTec non ha un'API pubblica: la
pagina usa GWT-RPC, troppo fragile da chiamare direttamente.

- In orario di apertura il worker importa **ogni 10 minuti**.
- Il tasto **Aggiorna ora** (pagina Spazzole della Distinta, report
  giornata, pagina Importa WashTec) lo fa partire **subito**: il worker
  controlla le richieste ogni ~15 secondi.
- Periodo di ogni giro: dal giorno prima dell'ultima transazione in
  archivio (almeno da ieri) a oggi. I doppioni sono ignorati.
- Ogni giro resta in *Admin > Import WashTec automatici* e nelle "Ultime
  esecuzioni" della pagina Importa WashTec; errori e ritardi compaiono in
  rosso accanto al tasto.

## Setup Railway

Nuovo **servizio** nel progetto Railway, dallo stesso repository del web
(non un cron: deve restare acceso per rispondere al tasto).

- **Build command:**
  `pip install -r requirements.txt && playwright install --with-deps chromium`
- **Start command:** `python manage.py importa_washtec_auto --ciclo`
- **Variabili:** le stesse del web per il database (`DATABASE_URL`,
  `SECRET_KEY`, ...) e in piu':

| Variabile | Valore |
|---|---|
| `WASHTEC_UTENTE` | utente di plus.washtec.com |
| `WASHTEC_PASSWORD` | password |
| `WASHTEC_ORA_INIZIO` | (facoltativa) ora locale di inizio, default `7` |
| `WASHTEC_ORA_FINE` | (facoltativa) ora locale di fine, default `22` |
| `WASHTEC_INTERVALLO_MIN` | (facoltativa) minuti tra un giro e l'altro, default `10` |

Il browser lavora nel fuso `Europe/Rome` e in italiano anche se il
server e' in UTC: WashTec mostra gli orari nel fuso del browser e
l'estrazione legge i testi italiani della pagina.

## Prova in locale

```bash
pip install playwright && playwright install chromium
export WASHTEC_UTENTE=... WASHTEC_PASSWORD=...
python manage.py importa_washtec_auto --dry-run --visibile   # legge senza salvare, browser visibile
python manage.py importa_washtec_auto --dal 2026-10-01 --al 2026-10-05   # recupera un periodo
```

## Se smette di funzionare

- *"Login WashTec non riuscito"*: credenziali cambiate o scadute.
- *"sezione Report non trovata"*, *"campi data non trovati"*, *"la
  tabella non avanza di pagina"*: WashTec ha cambiato la pagina; la
  correzione va in `static/js/washtec_estrazione.js` (vale anche per il
  preferito).
- *"Il servizio di aggiornamento WashTec non risponde"*: il worker su
  Railway e' spento o in errore (vedi i log del servizio).

Nel frattempo si puo' sempre importare a mano con il preferito dalla
pagina Finanze > Importa WashTec.
