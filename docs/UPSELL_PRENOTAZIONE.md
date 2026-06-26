# Upselling nella prenotazione online

Nello step di riepilogo del wizard di prenotazione (`/app/servizi/` step 4)
il cliente vede una sezione "Aggiungi extra al tuo lavaggio" che propone
servizi complementari e prodotti da scaffale. Gli item arrivano nello
stesso flusso della prenotazione e si trasformano in ItemOrdine al
check-in.

## Configurare cosa proporre

Django admin → **Servizi e Prodotti** (`/admin/core/servizioprodotto/`).
Per ciascun item che vuoi proporre come upsell:

1. Spunta **`Proponi in upsell`**.
2. Imposta **`Ordine upsell`** (0 = primo nella griglia, poi 1, 2, …).
3. Opzionalmente compila **`Upsell per`** con i servizi base per cui
   l'item va proposto:
   - **Vuoto** → upsell **universale**, mostrato sempre.
   - **Valorizzato** → mostrato solo quando il cliente ha selezionato
     almeno uno dei servizi indicati.

### Esempi tipici

| Item | `mostra_pubblico` | `proponi_in_upsell` | `upsell_per` |
|---|---|---|---|
| Lavaggio esterno (catalogo) | ✓ | – | – |
| Lavaggio completo (catalogo) | ✓ | – | – |
| Aspirazione interni | ✓ | ✓ | [Lavaggio esterno] |
| Cera cristallizzante | – | ✓ | [Lavaggio completo] |
| Profumatore vaniglia | – | ✓ | *vuoto* (universale) |
| Panno microfibra | – | ✓ | *vuoto* (universale) |

`mostra_pubblico` e `proponi_in_upsell` sono indipendenti: un prodotto
può apparire solo nell'upsell, un servizio può essere sia nel catalogo
principale sia nell'upsell.

## Comportamento lato cliente

- **Servizi extra**: bottone "Aggiungi/Rimuovi" (binari).
- **Prodotti**: controllo quantità `- N +` (0..9).
- Il totale di sezione mostra solo l'importo degli extra (i lavaggi
  restano "prezzo comunicato al ritiro", come da policy esistente).
- Cambiare i servizi base nello step 1 e tornare allo step 4
  ricarica il catalogo (i legami `upsell_per` cambiano).

## Comportamento lato gestionale

- **Card "Da confermare"** (`/ordini/` in alto): i prodotti extra
  appaiono come pillola viola con shopping-bag accanto ai servizi.
- **Modal check-in**: sezione read-only "Prodotti extra acquistati"
  sotto il box servizi. Si trasformano in ItemOrdine alla conferma
  check-in tramite `Prenotazione.converti_in_ordine`.
- **Dashboard cliente** (`/app/area/`): pillola viola accanto ai
  servizi della prenotazione.

## Scorte

Niente decremento automatico. Per i prodotti destinati all'upsell
lasciare `quantita_disponibile = -1` (illimitato, default) nell'admin:
il signal `aggiorna_scorte_prodotto` non tocca nulla. Se in futuro
serve gestire scorte finite di un prodotto, l'admin imposta un valore
positivo e il signal lo decremenenta automaticamente al check-in
(crea ItemOrdine).

## Note tecniche

- Endpoint catalogo: `GET /app/api/upsell/?servizi_scelti=<csv>`.
- Modello `PrenotazioneProdotto` (apps/prenotazioni): through-like con
  `quantita` e `prezzo_unitario` "fotografato" al momento dell'acquisto
  (protegge il cliente da cambi listino tra prenotazione e check-in).
- Conversione: `Prenotazione.converti_in_ordine` itera
  `prodotti_extra` e crea `ItemOrdine(quantita=p.quantita,
  prezzo_unitario=p.prezzo_unitario)`.
- I template WhatsApp di conferma prenotazione **non** includono i
  prodotti extra (i 4 parametri Meta approvati restano com'è). I
  prodotti compaiono solo nella pagina di riepilogo e nelle viste
  gestionali.
