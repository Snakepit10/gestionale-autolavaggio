# Previsione squadra

La pagina **Personale > Previsione squadra** stima i lavaggi del servito e consiglia quante persone mettere. Il calcolo è in `apps/personale/previsione.py`.

## Come funziona

- **Lavaggi previsti:** una regressione ridge sul logaritmo dei lavaggi, da: giorno della settimana, festivi e ponti, stagione, livello delle ultime 4 settimane e dell'ultima, pioggia prevista, pioggia dei 3 giorni prima, temperatura, sabbia del Sahara, prenotazioni note alle 19 del giorno prima.
- **Squadra:** lavaggi previsti + margine (parametro, di partenza 10%) / lavaggi per persona (parametro, di partenza 7), arrotondato per eccesso, più la persona al self.
- **Cicli self:** lo stesso modello, senza prenotazioni e con il livello dei cicli delle ultime settimane. Dai cicli dei portali si tolgono i lavaggi completi del servito. Una correzione riporta in pari i totali, che altrimenti verrebbero bassi di circa il 13%.
- **Persona al self:** al massimo una. Con il parametro "cicli previsti da cui serve la persona al self", sotto quel numero il self lo segue il servito. Vuoto vuol dire sempre. A servito chiuso resta coperto.

## Solo quello che si sa la sera prima

Il turno finisce alle 19: da quell'ora la giornata di oggi è completa ed entra nel livello con cui si prevede domani, come nell'addestramento, dove ogni giorno conosce quello prima. Prima delle 19 la pagina prevede domani senza la giornata di oggi.

Il modello impara e si verifica con la pioggia prevista il giorno prima, presa da Open-Meteo Previous Runs. La pioggia caduta non si usa per il giorno stesso: la sera prima non si conosce e farebbe sembrare l'errore più basso del vero.

Con circa 20 lavaggi al giorno il caso da solo vale circa il 17% di errore (0,8 × radice dei lavaggi). La pagina lo mostra accanto all'errore del modello: sotto quel valore non si può scendere.

Provati sui dati fino a novembre 2026 e non utili: giorni di stipendio, rimbalzo dopo la pioggia, media dello stesso giorno della settimana, pioggia nei giorni dopo, andamento delle piste.

## Quanto valgono i cicli self

Il 06/11/2026 sono state importate da WashTec le transazioni da agosto 2025 a giugno 2026. Provato con riaddestramento settimanale su 292 giorni, da gennaio al 5 novembre 2026: errore del 33% sul giorno, del 20% sulla settimana, dell'11% sul mese, con il totale al 99% del reale. Il caso da solo vale circa il 13% sul giorno: la distanza viene da picchi che il modello non vede, come 116 cicli contro 37 previsti domenica 10 maggio o i 70-80 cicli a fine luglio. La pioggia prevista conta poco.

Con una soglia di 25, 35 o 45 cicli la decisione persona sì o no è giusta il 74-79% dei giorni.

## Servizio meteo

Il servizio meteo si sceglie nella pagina fra sette modelli di Open-Meteo: ItaliaMeteo ICON-2I (di partenza), Open-Meteo automatico, ECMWF IFS, DWD ICON, Météo-France AROME/ARPEGE, UK Met Office e NOAA GFS. Per i giorni che il modello scelto non copre (ICON-2I arriva a circa 3 giorni) si usa quello automatico.

**Confronta i servizi meteo** riaddestra la previsione dei lavaggi con le previsioni del giorno prima di ciascun servizio e la verifica sugli stessi giorni: errore sui lavaggi, squadra giusta, giorni scoperti, turni risparmiati seguendo il modello. In più mostra quanti giorni di pioggia ogni servizio aveva previsto e quanti falsi allarmi ha dato rispetto all'archivio Open-Meteo, che è anch'esso una stima e non un pluviometro. Il confronto fa poche richieste, una o due per servizio, e resta in memoria 12 ore.

Il 06/11/2026 i servizi sono stati provati anche con riaddestramento settimanale su 265 giorni: nessuno batte ICON-2I, e nemmeno la pioggia caduta dell'archivio migliora la previsione.

**Avviso di pioggia:** se per domani il servizio in uso dà asciutto ma almeno 5 degli altri 9 modelli danno pioggia, la pagina lo segnala. Il numero di lavaggi e la squadra non cambiano. Nel 2026, in 32 giorni così, i lavaggi sono stati 16,2 di media contro 17,9 previsti.

**Risparmio seguendo il modello:** i turni al servito delle vostre scelte meno quelli del modello, negli stessi giorni della verifica, moltiplicati per le ore di un turno e il costo orario medio. La tabella sotto mostra anche i giorni scoperti.

## Giornata di pioggia

È di pioggia con almeno 1 mm oppure 2 ore di pioggia. Sotto 1 mm la pagina scrive "pioggia debole".

## Salvataggio ogni sera

Il confronto vero è fra la previsione salvata la sera prima e i lavaggi reali, in fondo alla pagina. La pagina salva quando viene aperta dopo le 19, e la prima salvata resta; per non dipendere da questo serve un **cron service** su Railway, come per `esegui_campagne_marketing`:

- Schedule: `10 17,18 * * *`
- Command: `python manage.py previsione_squadra`

Railway usa l'ora UTC, quindi d'inverno gira alle 18:10 e alle 19:10, d'estate alle 19:10 e alle 20:10. Il giro prima delle 19 non salva e quello dopo non riscrive: la previsione resta quella delle 19:10 tutto l'anno.
