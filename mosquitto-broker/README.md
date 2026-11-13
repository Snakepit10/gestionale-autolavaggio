# Broker MQTT — Mosquitto su Railway

Broker per l'impianto IoT dell'autolavaggio: i dispositivi in pista
(Shelly, Waveshare) e il CRM comunicano qui. Immagine ufficiale
`eclipse-mosquitto:2` + config di questo folder.

## Architettura

```
Shelly Plus Uni (pista2) ──┐
Waveshare (portale1) ──────┤  TCP Proxy Railway (pubblico, porta assegnata)
MQTT Explorer (debug) ─────┘        │
                                    ▼
                        [ mosquitto-broker :1883 ]
                                    ▲
CRM Django ────────── rete privata Railway (mosquitto-broker.railway.internal)
```

- Autenticazione obbligatoria (`allow_anonymous false`), un utente per
  client: `nodo_pista2`, `nodo_portale1`, `crm`, `debug`.
- Persistenza su volume Railway montato in `/mosquitto/data`.
- Il file `passwd` NON sta nel repo (vedi `.gitignore`): si genera in
  locale e si carica come **variabile d'ambiente** `MOSQUITTO_PASSWD`;
  l'entrypoint lo scrive in `/mosquitto/config/passwd` a ogni avvio.

## 1. Generare le password (in locale)

Servono `mosquitto_passwd` (incluso nell'installazione di Mosquitto,
oppure via Docker, vedi sotto) e un generatore di password robuste.

```bash
# Genera 4 password robuste e ANNOTALE in un password manager
openssl rand -base64 24   # ripeti 4 volte: nodo_pista2, nodo_portale1, crm, debug

# Crea il file passwd con un utente per client (ti chiede la password)
mosquitto_passwd -c passwd nodo_pista2
mosquitto_passwd    passwd nodo_portale1
mosquitto_passwd    passwd crm
mosquitto_passwd    passwd debug
```

Senza Mosquitto installato, via Docker:

```bash
docker run --rm -it -v "$PWD:/w" -w /w eclipse-mosquitto:2 \
    mosquitto_passwd -c passwd nodo_pista2
# ...e cosi' via per gli altri utenti (senza -c dal secondo in poi)
```

Il file `passwd` risultante contiene righe `utente:$7$...` (hash, non
password in chiaro). Resta comunque un segreto: **non committarlo**
(e' gia' in `.gitignore`).

## 2. Deploy su Railway

1. **Nuovo servizio** nel progetto Railway → *Deploy from GitHub repo* →
   questo repo → in *Settings → Source* imposta **Root Directory =
   `mosquitto-broker`** (il Dockerfile viene rilevato da solo).
2. **Volume**: *Settings → Volumes → Add volume* montato su
   **`/mosquitto/data`** (persistenza messaggi/sessioni).
3. **Variabile** `MOSQUITTO_PASSWD`: incolla il **contenuto integrale**
   del file `passwd` generato al punto 1 (tutte le righe, e' una
   variabile multiriga).
4. **TCP Proxy** (per far entrare i dispositivi da internet):
   *Settings → Networking → TCP Proxy* sulla porta **1883**.
   L'endpoint assegnato da Railway e' **`sakura.proxy.rlwy.net:46500`**
   (porta interna 1883): sono host e porta da configurare su
   Shelly/Waveshare e MQTT Explorer.
5. Il **CRM** invece parla col broker dalla rete privata (niente
   traffico pubblico): nelle variabili del servizio CRM imposta
   `MQTT_HOST=<nome-servizio>.railway.internal` (es.
   `mosquitto-broker.railway.internal`) e `MQTT_PORT=1883`.

Per **aggiungere/cambiare un utente** (es. un nuovo Shelly):
1. parti da un file `passwd` uguale al contenuto attuale di
   `MOSQUITTO_PASSWD` (altrimenti perdi gli utenti esistenti);
2. aggiungi l'utente **senza `-c`** (Docker, dalla cartella del file):
   `docker run --rm -it -v "${PWD}:/w" -w /w eclipse-mosquitto:2 mosquitto_passwd passwd nodo_pista3`
   e incolla la password quando la chiede (non si vede, e' normale);
   rilanciarlo su un utente esistente ne sostituisce la password;
3. incolla tutto il file in `MOSQUITTO_PASSWD` e riavvia il servizio.

## 3. Configurazione Shelly Plus Uni (nodo_pista2)

Gia' configurato con MQTT prefix `autolavaggio/pista2`. Sull'app/web UI
dello Shelly: Server = endpoint TCP Proxy (host:porta del punto 4),
utente `nodo_pista2` + password. Verifica che "RPC over MQTT" sia
attivo. Topic usati:

- pubblica su `autolavaggio/pista2/events/rpc` (NotifyStatus/NotifyEvent,
  incluso il contatore impulsi su input id 2)
- riceve comandi RPC su `autolavaggio/pista2/rpc`
  (es. `Switch.Set` sull'uscita indicata da `switch_id` del nodo:
  OUT1 = `id 0`, OUT2 = `id 1`; i nodi delle piste usano OUT2)

Auto-off: nella web UI dello Shelly apri l'uscita (OUT2 compare come
`out(1)` / "Output 1") → *Settings* → *Timers* → attiva **Auto off**
con **1 secondo**. Senza auto-off l'uscita resta accesa.

## 4. Collaudo con MQTT Explorer

1. Connessione: host/porta del TCP Proxy, utente `debug`, la sua
   password, niente TLS (protocollo `mqtt://`).
2. Alla connessione vedi l'albero `autolavaggio/…`; genera un impulso
   sul COUNT IN dello Shelly e osserva il NotifyStatus su
   `autolavaggio/pista2/events/rpc`.
3. Prova "moneta virtuale" a mano: pubblica su `autolavaggio/pista2/rpc`
   il payload
   `{"id":1,"src":"debug","method":"Switch.Set","params":{"id":1,"on":true}}`
   → OUT2 si accende e si spegne da solo dopo 1 s (auto-off hardware).
