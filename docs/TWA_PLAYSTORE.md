# TWA Android per Play Store — Guida operativa

App: **Autolavaggio MasterWash**
Package: `it.autolavaggiomasterwash.app`
Dominio: `https://autolavaggiomasterwash.it`
Scope PWA: `/app/`

---

## Prerequisiti lato server (gia fatti)

- `static/client-manifest.json` con `scope: /app/` e `start_url: /app/`
- `/.well-known/assetlinks.json` servito da Django (vuoto fino a configurazione env var)
- Template `clients/base_public.html` linka il manifest cliente
- HTTPS attivo sul dominio (Railway lo gestisce automaticamente)

---

## Lato utente: build APK con Bubblewrap

### 1. Installa Node.js + Bubblewrap

```bash
# Verifica Node >= 18
node --version

# Installa Bubblewrap CLI globalmente
npm install -g @bubblewrap/cli
```

### 2. Inizializza il progetto TWA

```bash
mkdir twa-masterwash
cd twa-masterwash

bubblewrap init --manifest https://autolavaggiomasterwash.it/static/client-manifest.json
```

Risponde alle domande:
- **Application ID**: `it.autolavaggiomasterwash.app`
- **Application name**: `Autolavaggio MasterWash`
- **Launcher name**: `MasterWash`
- **Display mode**: `standalone`
- **Orientation**: `portrait`
- **Theme color**: `#0d6efd`
- **Background color**: `#ffffff`
- **Generate icons from**: lascia default (usa quelle del manifest)
- **Signing key path**: lascia default (genera `android.keystore`)
- **Key alias**: `android`
- **Password keystore + alias**: SCEGLI UNA PASSWORD SICURA E SALVALA

**IMPORTANTE**: il file `android.keystore` generato e' critico. Senza non puoi
piu pubblicare aggiornamenti. **BACKUPPALO** su disco esterno + cloud.

### 3. Build APK + AAB firmati

```bash
bubblewrap build
```

Output:
- `app-release-signed.apk` — installabile direttamente su Android per test
- `app-release-bundle.aab` — questo va caricato su Play Store

### 4. Ottenere SHA256 fingerprint dal keystore

```bash
keytool -list -v -keystore android.keystore -alias android
```

Cerca la riga `SHA256:` e copia il valore (formato `XX:XX:XX:...`).

### 5. Configurare env var su Railway

Vai su Railway > Project > Variables, aggiungi:

```
TWA_ANDROID_PACKAGE_NAME=it.autolavaggiomasterwash.app
TWA_SHA256_FINGERPRINTS=AA:BB:CC:DD:EE:...
```

(Sostituisci AA:BB:... con il SHA256 effettivo)

Railway riavvia il service. Ora `/.well-known/assetlinks.json` ritorna i dati
corretti e la TWA non mostrera la URL bar.

### 6. Verifica assetlinks

Apri:
```
https://autolavaggiomasterwash.it/.well-known/assetlinks.json
```

Deve mostrare JSON con `package_name` e `sha256_cert_fingerprints` popolati.

Test con tool Google:
```
https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://autolavaggiomasterwash.it&relation=delegate_permission/common.handle_all_urls
```

### 7. Test APK su device Android

```bash
adb install app-release-signed.apk
```

Oppure trasferisci l'APK al telefono via USB/email e installa manualmente
(serve abilitare "App da fonti sconosciute" nelle impostazioni).

Apri l'app:
- Deve aprire `/app/` senza URL bar
- Deve mostrare il tuo logo MasterWash
- Test: prenotazione, login, navigazione

Se vedi la URL bar in alto -> assetlinks non e' configurato correttamente.

---

## Lato Play Console

### 1. Account Google Play Console

- Apri https://play.google.com/console
- Crea account developer: **€25 una tantum**
- Verifica identita (richiede documento)

### 2. Crea l'app

- "Crea app"
- Nome: `Autolavaggio MasterWash`
- Lingua predefinita: Italiano
- Tipo: App
- Gratuita o a pagamento: Gratuita
- Accetta dichiarazioni

### 3. Configurazione store

Compila i moduli (obbligatori):
- **Categoria**: Stile di vita o Affari
- **Descrizione breve** (80 char): "Prenota online il tuo lavaggio auto"
- **Descrizione completa** (~500-2000 char): cosa fa l'app
- **Privacy policy URL**: deve esistere una pagina (es. `/app/privacy/`)
- **Icona alta risoluzione 512x512 PNG**
- **Immagine principale 1024x500**
- **Screenshot smartphone**: min 2, max 8 (1080x1920 consigliato)

### 4. Carica l'AAB

- Test interno -> Crea nuova release
- Carica `app-release-bundle.aab`
- Note release: "Prima versione"
- Salva -> Rivedi release -> Invia per revisione

Google revisiona in **3-7 giorni** la prima volta. Aggiornamenti successivi
in 24-48h.

### 5. Test interno

Dopo l'approvazione del test interno, condividi il link con te stesso/team
per testare prima di pubblicare in produzione.

### 6. Promuovi a produzione

Quando OK: Internal -> Closed -> Production. Live sul Play Store in 24h.

---

## iOS (senza Mac)

### Opzione A — PWA Builder + MacInCloud
1. Vai su https://www.pwabuilder.com
2. Inserisci `https://autolavaggiomasterwash.it/app/`
3. Genera bundle iOS (progetto Xcode)
4. Affitta MacInCloud (~$1-2/h) o usa Mac di un amico
5. Apri progetto in Xcode, compila .ipa, upload su App Store Connect

### Opzione B — Cloud build service
Codemagic.io o EAS Build (Expo): build iOS in cloud senza Mac.
Prezzo: gratis con limiti, oppure ~30€/mese per pro.

Account Apple Developer: **€99/anno** (rinnovo annuale obbligatorio).

Apple revisiona ~1-3 giorni la prima volta, 24h successive.

### Universal Links (equivalente assetlinks per iOS)

Una volta configurato Apple Developer team:
```
IOS_APP_ID_PREFIX=ABCDEFGHIJ
IOS_BUNDLE_ID=it.autolavaggiomasterwash.app
```
nelle env var Railway. La rotta `/.well-known/apple-app-site-association`
e' gia configurata e si popola in automatico.

---

## Asset utili per gli store

### Genera screenshot

Apri `https://autolavaggiomasterwash.it/app/` in Chrome DevTools, attiva
device toolbar (Ctrl+Shift+M), imposta a 1080x1920, fai screenshot di:
- Landing
- Catalogo servizi
- Pagina prenotazione
- Area cliente
- Conferma prenotazione

### Privacy policy

Crea una pagina su `/app/privacy/` con dichiarazione GDPR sui dati raccolti:
- Email, telefono, nome cliente (per prenotazione)
- Cookie tecnici
- No tracking analytics terze parti
- Diritto cancellazione: contatta info@autolavaggiomasterwash.it

Va linkata da:
- Footer della PWA
- Form di registrazione
- Google Play Console (form obbligatorio)

---

## Aggiornamenti app

Vantaggio TWA: aggiornare il sito web = aggiornare l'app. Niente release
ad ogni piccola modifica.

Quando rilasciare nuova versione APK:
- Cambio del package name (mai)
- Cambio del manifest URL (es. nuovo dominio)
- Aggiornamenti SDK Android obbligatori da Google (di solito ogni 1-2 anni)
- Modifiche grafiche di icona/splash

Per aggiornare: incrementare `versionCode` e `versionName` in
`twa-manifest.json`, rifare `bubblewrap build`, upload AAB su Play Console.
