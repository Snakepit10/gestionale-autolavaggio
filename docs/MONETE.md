# Monete virtuali — guida operatore

Ogni cliente ha un **saldo di monete virtuali** con cui avvia i lavaggi
self-service: il CRM verifica il saldo, lo scala e manda gli impulsi
"gettoniera" al nodo via MQTT (vedi impianto IoT). Le monete si
caricano in cassa, si regalano con le promozioni o si comprano online
(Stripe/PayPal).

## 1. Configurare i nodi (admin → Nodi impianto)

Un nodo = un punto di erogazione comandabile (pista, portale...).

| Campo | Significato |
|---|---|
| slug | segmento del topic MQTT: deve combaciare con l'MQTT prefix `autolavaggio/<slug>` dello Shelly (es. `pista2`) |
| switch_id | uscita del relè gettoniera (pista2: OUT2 = id 1) |
| monete_per_impulso | **l'economia del nodo**: quante monete costa un impulso |
| max_impulsi | tetto per singola operazione (default 10) |
| attivo | spunta per farlo comparire nelle liste |

## 2. Gestire il saldo di un cliente (staff)

Da **scheda cliente → card "Monete Virtuali" → Gestisci**:

- **Ricarica in cassa**: il cliente paga in sede; l'**importo in euro è
  obbligatorio** e resta sul movimento (riconciliabile con la cassa).
- **Regalo / Promozione**: accrediti gratuiti con causale.
- **Rettifica**: correzioni manuali, in aggiunta o rimozione.

Ogni operazione registra un movimento con saldo risultante e operatore:
lo storico completo è nella card e in admin → Movimenti monete.

## 3. Avviare un lavaggio

- **Staff**: scheda cliente → "Avvia lavaggio", oppure `/monete/avvia/`
  (ricerca cliente). Scegli nodo e impulsi: il costo si aggiorna live.
- **Cliente dall'app**: `/app/monete/` → "Avvia un lavaggio". Stesso
  flusso, con avviso se il saldo non basta.

Regole di sicurezza (automatiche):
- le monete vengono scalate PRIMA di inviare gli impulsi; se il broker
  non risponde o la sequenza si interrompe, gli impulsi non erogati
  vengono **stornati** in automatico (movimento "Storno");
- doppio tap/doppio submit → un solo addebito (chiave di idempotenza);
- due avvii ravvicinati sullo stesso nodo → bloccati per
  `cooldown_lavaggio_sec` (admin → Impostazioni monete, default 15s;
  lo staff può forzare).

## 4. Vendita online (pacchetti)

1. Admin → **Pacchetti monete**: crea i tagli (es. "10 monete — 10 €",
   con eventuale bonus).
2. Admin → **Impostazioni monete**: attiva `vendita_online_attiva` e i
   provider che vuoi usare (`stripe_attivo`, `paypal_attivo`).
3. Variabili d'ambiente su Railway (servizio CRM):

| Variabile | Dove si trova |
|---|---|
| `STRIPE_SECRET_KEY` | dashboard.stripe.com → Developers → API keys (`sk_live_...`; `sk_test_...` per le prove) |
| `STRIPE_WEBHOOK_SECRET` | Stripe → Developers → Webhooks → endpoint `https://<dominio>/monete/webhook/stripe/`, evento `checkout.session.completed` → signing secret `whsec_...` |
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | developer.paypal.com → app REST (Live) |
| `PAYPAL_BASE_URL` | `https://api-m.paypal.com` in produzione (default = sandbox) |

I bottoni di acquisto compaiono nell'area cliente solo quando flag e
chiavi sono presenti.

**Come arrivano le monete**: Stripe accredita via webhook (con
fallback sulla pagina di ritorno, quindi funziona anche se il webhook
tarda); PayPal accredita al ritorno del cliente. Rete di sicurezza
PayPal: il comando cron

```
python manage.py monete_riconcilia
```

(da aggiungere al cron esistente ogni 15 min) cattura i pagamenti
approvati dai clienti che hanno chiuso il browser prima del ritorno.
Tutti gli accrediti sono idempotenti: webhook ripetuti o refresh della
pagina non raddoppiano mai il saldo.

**Rimborsi**: fuori dal flusso automatico. Rimborsa dal pannello
Stripe/PayPal e allinea il saldo con una Rettifica manuale.

## 5. Collaudo end-to-end (pista2)

1. Admin → Nodi impianto: `pista2`, switch_id 1, 1 moneta/impulso.
2. Scheda cliente → Gestisci monete → ricarica 5 monete (importo 5 €).
3. "Avvia lavaggio" → 2 impulsi → il relè OUT2 scatta 2 volte, il
   saldo scende, il contatore sale in admin → Eventi impianto.
4. Dall'app cliente: login → Monete → Avvia → 1 impulso.
5. Doppio submit rapido → un solo addebito. Saldo a 0 → avvio rifiutato.
6. Stripe in test: chiavi `sk_test`, pacchetto, carta `4242 4242 4242
   4242` → monete accreditate; replay del webhook dal pannello Stripe →
   saldo invariato.
7. PayPal sandbox: acquisto con buyer di prova; poi ripeti chiudendo il
   browser dopo l'approvazione e lancia `monete_riconcilia` → accredito
   recuperato.

## 6. Problemi comuni

| Sintomo | Causa probabile |
|---|---|
| "Impianto non raggiungibile" | variabili `MQTT_*` mancanti sul CRM o broker giù |
| Avvio ok ma il relè non scatta | slug nodo ≠ MQTT prefix dello Shelly, o switch_id sbagliato |
| Monete scalate senza impulsi | mai: lo storno automatico le restituisce (vedi movimento "Storno") |
| Acquisto pagato ma saldo fermo | Stripe: webhook non configurato → basta riaprire la pagina di esito; PayPal: attendere il cron `monete_riconcilia` |
| Bottoni acquisto assenti | `vendita_online_attiva` spento, flag provider spenti o chiavi env mancanti |
