# WhatsApp Cloud API — Setup MasterWash

Guida completa per attivare le notifiche prenotazione via WhatsApp
usando l'API ufficiale di Meta (la più economica al volume: 1000
conversazioni Utility/mese gratis, poi ~€0.04/conv in Italia).

Senza configurazione il sistema cade automaticamente sulle email come
prima (`apps/clients/notifications.py` -> `email_prenotazione_*`).

---

## Cosa si invia su WhatsApp

5 notifiche, tutte con template HSM Meta-approvati (categoria Utility):

| Trigger | Quando | Template |
|---|---|---|
| Ricevuta | Cliente invia richiesta dal form PWA | `prenotazione_ricevuta` |
| Confermata | Operatore accetta dalla pagina ordini | `prenotazione_confermata` |
| Rifiutata | Operatore rifiuta con motivo | `prenotazione_rifiutata` |
| Modificata | Operatore sposta lo slot ad altra data/ora | `prenotazione_modificata` |
| Promemoria | Cron 45–90 min prima dello slot (oggi) | `prenotazione_promemoria` |

I 4 testi email originali restano come fallback se WhatsApp fallisce
(rete down, token scaduto, template rifiutato, numero non valido).

---

## STEP 1 — Account Meta Business

1. Vai su [business.facebook.com](https://business.facebook.com) e
   crea un Business Manager nominandolo es. "Autolavaggio MasterWash".
2. Verifica il business: Meta richiede documento d'impresa (visura o
   simile). Tempo di approvazione: 3–7 giorni.
3. Dentro Business Manager: **Account** → **Account WhatsApp** →
   crea un WhatsApp Business Account (WABA).

## STEP 2 — Numero WhatsApp sender

⚠️ Critico: il numero che userai per inviare i messaggi NON deve
essere già attivo in:
- WhatsApp normale (l'app personale)
- WhatsApp Business app

Se 379 233 7051 è già registrato in una di queste:
1. Apri l'app WhatsApp/WhatsApp Business sul telefono
2. Impostazioni → Account → **Elimina il mio account**
3. Aspetta 30 minuti

Dopo:
1. In Meta Business → WABA → **Aggiungi numero**
2. Inserisci 379 233 7051 e verifica via SMS o chiamata
3. Annota il **Phone Number ID** (es. `123456789012345`) → lo useremo
   come `META_WHATSAPP_PHONE_ID`
4. Annota il **WhatsApp Business Account ID** → lo useremo come
   `META_WHATSAPP_BUSINESS_ACCOUNT_ID`

## STEP 3 — Meta App for Developers

1. Vai su [developers.facebook.com](https://developers.facebook.com)
2. My Apps → **Create App** → tipo "Business"
3. All'interno dell'app: **Add product** → **WhatsApp**
4. Collega l'app al Business Manager del Step 1
5. Nella sezione WhatsApp → **API Setup** vedrai un access token
   **temporaneo** (scade ogni 24h). Va bene per i primi test; per
   produzione segui Step 4.

## STEP 4 — System User permanent token

Il token temporaneo va sostituito con un token a vita lunga:

1. Business Manager → **Impostazioni** → **Utenti del sistema**
2. **Aggiungi**: nome "MasterWash Backend", ruolo Amministratore
3. **Genera nuovo token** → seleziona la tua App Meta → permessi:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
4. Salva il token (lo vedi UNA volta sola): è il
   `META_WHATSAPP_ACCESS_TOKEN`
5. Dalla pagina del System User, **Assegna asset** → seleziona la
   WABA → permesso "Gestisci"

## STEP 5 — Template HSM (Message Templates)

In Meta Business → WhatsApp Manager → **Modelli messaggi** crea i 5
template seguenti. Categoria sempre **UTILITY** (notifiche
transazionali), lingua **Italiano (it)**:

### prenotazione_ricevuta
```
Body:
Ciao {{1}}! Abbiamo ricevuto la tua richiesta di prenotazione per
il {{2}} alle {{3}}. Servizi: {{4}}. Codice: {{5}}.
Confermeremo a breve.
```

### prenotazione_confermata
```
Body:
Ciao {{1}}! La tua prenotazione del {{2}} alle {{3}} è CONFERMATA.
Codice: {{4}}. Ti aspettiamo a Via Palma 302, Licata.
Per modifiche: 379 233 7051.
```

### prenotazione_rifiutata
```
Body:
Ciao {{1}}, non possiamo confermare la prenotazione del {{2}} alle
{{3}}. Motivo: {{4}}. Riprova scegliendo un'altra fascia oraria.
Ci scusiamo per il disagio.
```

### prenotazione_modificata
```
Body:
Ciao {{1}}! La prenotazione è stata spostata da {{2}} a {{3}}
alle {{4}}. Codice: {{5}}. Per modifiche telefonaci al
379 233 7051.
```

### prenotazione_promemoria
```
Body:
Ciao {{1}}! Ti ricordiamo la prenotazione di OGGI alle {{2}}.
Codice: {{3}}. Via Palma 302, Licata. A tra poco!
```

Approvazione Meta: 24–72h. Se rifiutati per "informazioni di
business mancanti", aggiungi descrizione precisa in Manager (es.
"Conferma prenotazione lavaggio auto") e ri-sottoponi.

---

## STEP 6 — Env vars su Railway

Project → **Variables** → aggiungi (sostituendo con i tuoi valori):

```
META_WHATSAPP_PHONE_ID=123456789012345
META_WHATSAPP_BUSINESS_ACCOUNT_ID=987654321098765
META_WHATSAPP_ACCESS_TOKEN=EAAAxxxxxxx...
META_WHATSAPP_API_VERSION=v21.0
META_WHATSAPP_TEMPLATE_LANG=it
```

Override opzionali (solo se Meta cambia i nomi dei template):

```
META_WA_TEMPLATE_RICEVUTA=prenotazione_ricevuta
META_WA_TEMPLATE_CONFERMATA=prenotazione_confermata
META_WA_TEMPLATE_RIFIUTATA=prenotazione_rifiutata
META_WA_TEMPLATE_MODIFICATA=prenotazione_modificata
META_WA_TEMPLATE_PROMEMORIA=prenotazione_promemoria
```

Dopo il redeploy, nei log del backend Django dovrai vedere alla
prima notifica `WhatsApp inviato to=+39... template=...`.

---

## STEP 7 — Railway Cron service per i promemoria

I promemoria 45–90 min prima dello slot richiedono uno scheduler
esterno (Django non ha cron nativo).

1. Su Railway: **New** → **Empty Service**
2. **Source**: stesso repo del backend
3. **Settings** → **Service Name**: `promemoria-cron`
4. **Settings** → **Cron Schedule**: `*/15 * * * *` (ogni 15 minuti)
5. **Settings** → **Start Command**:
   ```
   python manage.py invia_promemoria_prenotazioni
   ```
6. Stesse env vars del backend principale (Railway le condivide se
   il servizio fa parte dello stesso progetto)
7. Deploy

Verifica: dopo che il primo cron tick gira, controlla i log del
servizio promemoria-cron. Atteso: `0 promemoria da inviare
(finestra 45-90 min).` o `OK promemoria CODICE slot=...`.

Il comando è idempotente (`promemoria_inviato=True` previene
duplicati), quindi anche se gira più volte sulla stessa
prenotazione il messaggio parte una sola volta.

---

## Test in sandbox (prima di andare live)

Meta WhatsApp Cloud API offre numeri di test pre-approvati:

1. In Meta Developers App → WhatsApp → **API Setup**
2. Sezione "Send and receive messages": fornisce un Phone ID di test
   e fino a 5 numeri destinatari pre-approvati
3. Aggiungi il tuo numero personale come tester
4. Imposta le env vars con il Phone ID di test + token temporaneo
5. Fai una prenotazione dal browser PWA con il tuo numero come
   contatto → ricevi WhatsApp sul tuo telefono

In sandbox **non servono template approvati**: Meta accetta i 5 nomi
"hello_world" e simili pre-caricati. Per produzione invece servono
i tuoi template approvati (Step 5).

---

## Troubleshooting

| Sintomo | Causa probabile | Fix |
|---|---|---|
| Log: `WhatsApp send fallito (401)` | Token scaduto o errato | Rigenera System User token (Step 4) |
| Log: `WhatsApp send fallito (400) template not found` | Nome template diverso | Verifica i nomi nelle env vars `META_WA_TEMPLATE_*` |
| Log: `WhatsApp send fallito (400) parameter count mismatch` | Template Meta ha N variabili, il codice ne manda M | Controlla che il body template in Meta Manager corrisponda ai parametri inviati da `apps/clients/whatsapp.py` |
| Log: `WhatsApp send fallito (404) phone number not registered` | Il numero destinatario non ha WhatsApp | Atteso: caduta su email fallback automatica |
| Niente nei log e niente email | `WHATSAPP_ENABLED=False` per env vars mancanti, ed email backend non configurato | Verifica DJANGO settings EMAIL_HOST_* |
| Promemoria non parte | Cron service non running o env vars non condivise | Railway → promemoria-cron → Logs → controlla che il cron tick stia girando |

---

## Costi attesi (Italia, Utility)

- 0–1000 conversazioni/mese: **gratis** (Meta tier free)
- Oltre: ~€0.04/conv

Una "conversazione" = finestra di 24h con un cliente. 5 notifiche
allo stesso cliente nello stesso giorno = 1 conversazione = 1
addebito. Volume tipico: 100 prenotazioni/mese → 100–200 conv,
ampiamente dentro il free tier.

---

## STEP 8 — Ricezione messaggi (inbox bidirezionale)

Per leggere le risposte dei clienti e poter rispondere dal gestionale
serve configurare un **webhook**: Meta chiama il nostro backend ogni
volta che arriva un messaggio o un cambio di stato consegna/letto.

### 8.1 Env vars su Railway

```
META_WHATSAPP_VERIFY_TOKEN = <una stringa segreta a scelta, es. mw-wa-verify-2026>
META_WHATSAPP_APP_SECRET   = <App Secret della tua Meta App>
```

L'App Secret si trova in [Meta Developers App](https://developers.facebook.com)
→ la tua app → **Settings → Basic → App Secret → Show**.

### 8.2 Configura il webhook in Meta

In Meta Developers App → **WhatsApp → Configuration**:

1. **Callback URL**:
   `https://autolavaggiomasterwash.it/api/whatsapp/webhook/`
2. **Verify token**: stesso valore di `META_WHATSAPP_VERIFY_TOKEN`
3. Click **Verify and save** → Meta fa GET con `hub.challenge`,
   il backend risponde con il challenge → ✅ verde.
4. In **Webhook fields** della WABA → **Subscribe** al campo
   `messages`.

### 8.3 Test

1. Dal tuo telefono manda un WhatsApp al numero business
   `+39 379 233 7051`.
2. Log Railway atteso:
   ```
   INFO ... WhatsApp webhook ricevuto ...
   ```
3. Apri `/messaggi/` come staff → vedi la nuova conversazione
   con badge "1 non letto".
4. Click → leggi → digita risposta → "Invia" → ✓ (sent),
   poi ✓✓ (delivered), poi ✓✓ blu (read).

### 8.4 Finestra 24h

Per inviare **testo libero** WhatsApp richiede che il cliente abbia
scritto entro 24h. Oltre quel limite il textarea si disabilita e
l'UI mostra un warning: per riavviare la conversazione si deve
inviare un template (le 5 notifiche prenotazione del gestionale
fanno questo automaticamente).

### 8.5 Numeri sconosciuti

I messaggi da numeri non in anagrafica appaiono come "Numero
sconosciuto" con un bottone **Aggancia cliente** nell'header chat:
click → cerca per nome/telefono → seleziona → la conversazione viene
collegata e il nome cliente appare nell'inbox.

### Troubleshooting webhook

| Sintomo | Causa | Fix |
|---|---|---|
| Setup "Verify and save" → 403 | Verify token Railway ≠ Meta | Riallinea `META_WHATSAPP_VERIFY_TOKEN` su Railway |
| Webhook POST risponde 403 in produzione | App Secret errato (firma X-Hub non matcha) | Verifica `META_WHATSAPP_APP_SECRET` su Railway = App Secret della tua app Meta |
| Messaggi arrivano ma non in `/messaggi/` | Subscribe a `messages` mancato | In WABA → Webhooks → click "Subscribe" sul campo `messages` |
| Inbox vuota anche dopo POST | Numero E.164 non parsabile | Log Railway: cerca `_to_e164` ritornato None. Probabile prefisso strano |
| Risposta operatore restituisce 400 "finestra_24h_scaduta" | Cliente non scrive da >24h | Atteso: invia un template per riavviare la chat |
