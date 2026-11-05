# Previsione squadra

La pagina **Personale > Previsione squadra** stima i lavaggi del servito e consiglia quante persone mettere. Il calcolo è in `apps/personale/previsione.py`.

## Come funziona

- **Lavaggi previsti:** una regressione ridge sul logaritmo dei lavaggi, da: giorno della settimana, festivi e ponti, stagione, livello delle ultime 4 settimane e dell'ultima, pioggia prevista, pioggia dei 3 giorni prima, temperatura, sabbia del Sahara, prenotazioni note alle 18 del giorno prima.
- **Squadra:** lavaggi previsti + margine (parametro, di partenza 10%) / lavaggi per persona (parametro, di partenza 7), arrotondato per eccesso, più una persona al self.

## Solo quello che si sa la sera prima

Il modello impara e si verifica con la pioggia prevista il giorno prima, presa da Open-Meteo Previous Runs. La pioggia caduta non si usa per il giorno stesso: la sera prima non si conosce e farebbe sembrare l'errore più basso del vero.

Con circa 20 lavaggi al giorno il caso da solo vale circa il 17% di errore (0,8 × radice dei lavaggi). La pagina lo mostra accanto all'errore del modello: sotto quel valore non si può scendere.

Provati sui dati fino a novembre 2026 e non utili: giorni di stipendio, rimbalzo dopo la pioggia, media dello stesso giorno della settimana, pioggia nei giorni dopo, andamento delle piste.

## Giornata di pioggia

È di pioggia con almeno 1 mm oppure 2 ore di pioggia. Sotto 1 mm la pagina scrive "pioggia debole".

## Salvataggio ogni sera

Il confronto vero è fra la previsione salvata la sera prima e i lavaggi reali, in fondo alla pagina. La pagina salva quando viene aperta dopo le 18; per non dipendere da questo serve un **cron service** su Railway, come per `esegui_campagne_marketing`:

- Schedule: `5 17 * * *` (Railway usa l'ora UTC: sono le 18:05 d'inverno e le 19:05 d'estate)
- Command: `python manage.py previsione_squadra`
