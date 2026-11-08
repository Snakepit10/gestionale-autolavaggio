"""Le caselle di informazioni degli indicatori (tag {% info %} delle pagine
di contabilita'): nome come appare in pagina -> come si calcola, cosa
indica e, se serve, a cosa fare attenzione."""

SPIEGAZIONI = {
    # ---------------------------------------------------------- conto economico
    'Ricavi netti': (
        'In base a quanto scelto in alto. Rilevato reale: servito e prodotti = totale ordini meno l\'IVA del registratore; cambia gettoni e monete online = fatturato diviso 1,22; i portali sono il resto dei soldi delle quadrature meno l\'IVA delle loro chiusure di cassa. '
        'Scontrini fiscali: registratore fiscale (servito e prodotti) e casse automatiche, diviso 1,22: il punto di vista fiscale. '
        'Con questa base il personale conta solo busta paga e contributi, senza fuori busta e bonus.',
        'Quanto ha incassato davvero l\'azienda, IVA esclusa.',
        'Le due basi possono dare numeri diversi: se la differenza è grande, una delle due fonti è incompleta.'),
    'Totale ricavi': ('Somma dei ricavi dei canali, IVA esclusa.', 'Il volume d\'affari del periodo.'),
    'Margine di contribuzione': (
        'Ricavi − costi variabili (acqua, energia, chimici, materiali, prodotti, commissioni POS).',
        'Quanto resta di ogni euro venduto per pagare costi fissi e personale e fare utile.'),
    'EBITDA': (
        'Ricavi − costi variabili − costi fissi − personale. Prima di ammortamenti, interessi e imposte.',
        'Quanto guadagna la gestione ordinaria: è la misura della capacità di fare cassa usata dalle banche.',
        'I mesi con fatture non ancora caricate risultano più alti del vero.'),
    'EBIT': (
        'EBITDA − ammortamenti.',
        'Il risultato della gestione dopo aver pagato anche il consumo degli impianti: dice se l\'attività si ripaga '
        'nel lungo periodo.'),
    'Risultato prima delle imposte': ('EBIT − oneri finanziari.', 'L\'utile prima delle tasse.'),
    'Risultato netto': (
        'Risultato prima delle imposte − imposte e tasse registrate.',
        'Quello che resta alla fine.',
        'Comprende solo le imposte registrate: se IRPEF, IRAP e INPS del titolare non ci sono, è più alto del vero.'),

    # ------------------------------------------------------------ flussi di cassa
    'Flusso gestione operativa': (
        'Entrate e uscite della gestione ordinaria dai conti: incassi, fornitori, stipendi, IVA, imposte.',
        'Se l\'attività di tutti i giorni genera o assorbe cassa.',
        'I movimenti ancora da classificare restano qui.'),
    'Flusso investimenti': ('Uscite per impianti e attrezzature.', 'Quanta cassa è andata negli investimenti.'),
    'Flusso finanziamenti e titolare': (
        'Erogazioni e rate dei finanziamenti, interessi, prelievi e versamenti del titolare.',
        'Quanta cassa arriva da banche e titolare, o torna a loro.'),

    # ------------------------------------------------------------------ analitica
    'Margine del centro': (
        'Ricavi del centro − i costi attribuiti al centro (variabili, fissi, personale, ammortamenti).',
        'Quanto guadagna ogni attività prima di dividere i costi comuni. La colonna Comune raccoglie i costi non attribuiti.'),
    'Quota costi comuni': (
        'Ogni costo rimasto sul Comune va sui centri con il criterio della sua voce o del suo cespite: ricavi del centro, '
        'cicli dei portali (self contro servito) oppure ore del personale. Il personale sul Comune va per ore, le fatture '
        'da classificare per ricavi. Nella colonna Comune la stessa cifra esce con il segno meno.',
        'La parte di struttura che ogni attività deve pagare.',
        'Se un criterio non ha base (es. nessun ciclo registrato) quel costo va per ricavi. I criteri si cambiano nelle '
        'impostazioni dell\'analitica.'),
    'Costo per ciclo di portale': (
        'Costi comuni divisi per cicli (manutenzione dei portali, chimici, acqua…) / cicli totali dei portali, self più servito.',
        'Il prezzo interno di un passaggio al portale: il servito lo paga per ogni ciclo che usa.'),
    'Margine di contribuzione I': (
        'Ricavi del self-service − i suoi costi variabili.',
        'Quanto resta dalle vendite del self dopo i consumi diretti.'),
    'Margine di contribuzione II': (
        'Margine I − costi fissi specifici del self (manutenzione, quota del personale, ammortamenti dei portali e delle piste).',
        'Quanto il self-service contribuisce a pagare i costi comuni e a fare utile.'),
    'Margine diretto': (
        'Ricavi del servito − i suoi costi diretti (quota del personale, consumabili, tutto ciò che è attribuito al centro Servito).',
        'Quanto rende il servito prima di pagare la sua parte di struttura.'),
    'Risultato pieno': (
        'Margine del centro − quota dei costi comuni. La somma dei risultati pieni dei centri è l\'EBIT del conto economico.',
        'L\'utile vero di ogni attività, dopo aver pagato anche assicurazioni, manutenzioni comuni e tutta la struttura per la sua parte.'),
    'Cicli portali': (
        'Cicli scritti nelle chiusure dei due portali nel periodo, meno i cicli del servito (che passano anche loro dal '
        'portale). Sotto il valore si vedono i cicli totali e quelli del servito.',
        'Quanti lavaggi hanno fatto i clienti self ai portali a spazzole.',
        'Le piste non contano. I giorni senza chiusura della cassa non entrano nel conteggio. I cicli del servito sono '
        'come nel report giornata: un ciclo per ogni servizio con programmi portale, tranne quelli chiusi senza portale, '
        'più gli abbinati a mano.'),
    'Ricavo / ciclo': (
        'Ricavi dei portali, IVA esclusa, diviso i cicli self. Con il rilevato reale i ricavi dei portali sono il resto '
        'del rilevato, tolti gli ordini e il cambia gettoni, meno l\'IVA dei portali.',
        'Quanto rende in media un passaggio al portale: dice se prezzi e scelta dei programmi funzionano.'),
    'Costo var. / ciclo': (
        'Tutti i costi variabili del self-service diviso i cicli self.',
        'Quanto costa in consumi un lavaggio al portale.',
        'È indicativo e un po\' più alto del vero: i costi variabili comprendono anche le piste.'),
    'Cicli di pareggio': (
        'Costi fissi specifici del self / margine I per ciclo.',
        'Quanti cicli servono nel periodo per pagare i costi fissi del self-service.',
        'Indicativo: il margine I comprende anche le piste, ma i cicli sono solo quelli self dei portali.'),
    'Lavaggi': (
        'Ordini del periodo non annullati con almeno un servizio, escluse le vendite di soli prodotti. Ogni ordine conta 1, '
        'anche con più servizi. Gli accessi in abbonamento contano (l\'auto si lava), la vendita dell\'abbonamento no.',
        'Il volume di lavoro del servito.'),
    'Costo pieno / lavaggio': (
        '(Costi diretti del servito + quota dei costi comuni) / lavaggi. Dentro ci sono personale, consumabili, ammortamenti '
        'del centro e la sua parte di struttura; non ci sono interessi e imposte.',
        'Quanto costa in media un lavaggio considerando tutto. Va confrontato con il ricavo per lavaggio qui sotto: '
        'se il ricavo è più basso, il servito perde.',
        'È una media: i mesi senza tutte le fatture lo abbassano.'),
    'Ore lavorate servito': (
        'Ore del calendario presenze × quota sul servito di ogni dipendente (Ripartizioni del personale). Ferie e malattia '
        'non contano.',
        'Le ore pagate per il servito.'),
    'Costo pieno / ora': (
        'Lo stesso costo pieno del servito (diretti + quota comuni) / ore lavorate servito.',
        'Quanto costa un\'ora di lavoro del servito con tutto compreso: non è lo stipendio orario. Va confrontato con il '
        'ricavo per ora qui sotto.'),
    'Costo pieno del servizio': (
        'Costo pieno / ora × minuti del servizio nel catalogo.',
        'Una stima di quanto costa quel servizio, da confrontare con il prezzo.',
        'Vale un\'ora-persona per ogni ora di catalogo: se un\'auto la lavorano più persone insieme, il costo vero è più alto.'),

    # ------------------------------------------------------- analitica: produttivita'
    'Ore pagate servito': (
        'Ore lavorate del calendario presenze × quota sul servito di ogni dipendente. Ferie e malattia non contano.',
        'Le ore che si pagano per il servito, lavorate o di attesa.'),
    'Auto per ora-persona': (
        'Lavaggi / ore pagate servito.',
        'La misura principale della produttività di un autolavaggio con servito: quante auto escono per ogni ora pagata.',
        'Scende quando c\'è più gente in turno di quanta ne serva per le auto che arrivano.'),
    'Persone medie al giorno': (
        'Giorni-persona del servito / giorni con almeno un lavaggio. Un giorno-persona è una presenza con ore, per la '
        'quota servito del dipendente.',
        'Quante persone c\'erano in media al servito nei giorni aperti.'),
    'Lavaggi per persona al giorno': (
        'Lavaggi / giorni-persona del servito.',
        'Quante auto ha lavato in media ogni persona in una giornata. Si confronta con il pareggio qui sotto.'),
    'Ricavo per lavaggio': (
        'Ricavi del servito (IVA esclusa, stessa base della scheda Centri) / lavaggi.',
        'Lo scontrino medio del servito. Gli accessi in abbonamento contano come lavaggi a zero euro.'),
    'Consumi per lavaggio': (
        'Costi variabili del servito (chimici, materiale, energia…) / lavaggi.',
        'Quanto si spende in consumi per ogni auto.'),
    'Costo pieno per lavaggio': (
        'Costi del servito + sua quota dei costi comuni / lavaggi.',
        'Quanto costa in media un lavaggio, struttura compresa.'),
    'Risultato per lavaggio': (
        'Ricavo per lavaggio − costo pieno per lavaggio.',
        'Quanto guadagna (o perde) in media il servito su ogni auto.'),
    'Costo del lavoro sui ricavi': (
        'Costo del personale del servito / ricavi del servito × 100.',
        'Quanta parte di ogni euro incassato va in stipendi e contributi.',
        'Come ordine di grandezza indicativo, i lavaggi con servito puntano di solito sul 35-45%.'),
    'Ricavi servito': ('Ricavi del servito, IVA esclusa, con la base scelta.', 'Il fatturato del servito nel mese.'),
    'Risultato pieno servito': (
        'Ricavi del servito − costi del servito − sua quota dei costi comuni.',
        'L\'utile del servito nel mese, struttura compresa.'),
    'Lavaggi con extra': (
        'Ordini di lavaggio con almeno due righe di servizio / lavaggi. Il principale è la riga col prezzo più alto.',
        'Quanto spesso il cliente aggiunge qualcosa al lavaggio: misura le vendite aggiuntive.'),
    'Ricavo extra per lavaggio': (
        'Somma delle righe di servizio diverse dalla principale, IVA esclusa / lavaggi.',
        'Quanto portano in media gli extra su ogni auto.'),

    # -------------------------------------------------------- indebitamento massimo
    'Nuovo finanziamento ottenibile': (
        'Il più basso fra il debito ripagabile con la rata ancora disponibile (DSCR) e lo spazio del multiplo dell\'EBITDA.',
        'Quanto si può chiedere ancora alle banche con le ipotesi impostate.',
        'Le banche guardano il bilancio fiscale: con ricavi dichiarati più bassi il valore scende.'),
    'Indebitamento massimo sostenibile': (
        'Debito di oggi + nuovo finanziamento ottenibile. Senza spazio: il più basso fra il debito sostenibile con le rate '
        'e PFN massima + liquidità.',
        'Il debito totale che l\'azienda può reggere.'),
    'Flusso al servizio del debito': (
        'EBITDA − imposte − investimenti di mantenimento.',
        'I soldi che restano ogni anno per pagare le rate.'),

    # ---------------------------------------------------------------- redditivita'
    'Crescita dei ricavi': (
        'Ricavi del periodo / ricavi dello stesso periodo dell\'anno prima − 1.',
        'Quanto cresce l\'attività.',
        'Nei primi anni, o se l\'anno prima ha pochi dati, è molto alta e dice poco.'),
    'EBITDA margin': ('EBITDA / ricavi.', 'Quanti centesimi di margine operativo restano su ogni euro venduto. Sopra il 20% è buono.'),
    'ROS (EBIT margin)': ('EBIT / ricavi.', 'La redditività dopo il consumo degli impianti. Sopra il 10% è buono.'),
    'Utile netto sui ricavi': ('Risultato netto / ricavi.', 'Quanti centesimi di utile restano su ogni euro venduto.'),
    'ROI sugli impianti': (
        'EBIT annuo / valore netto dei cespiti.',
        'Quanto rende il capitale investito negli impianti. Va confrontato con quanto costerebbe il denaro in banca.'),
    'Rotazione degli impianti': ('Ricavi annui / valore d\'acquisto dei cespiti.', 'Quanti euro di ricavo produce ogni euro investito.'),

    # ------------------------------------------------------------ struttura dei costi
    'Incidenza del personale': ('Costo del personale / ricavi.', 'Quanta parte dei ricavi va in stipendi. Sopra il 50% è critico.'),
    'Incidenza dei costi fissi': (
        '(Costi fissi + personale + ammortamenti) / ricavi.',
        'Quanto è rigida la struttura: più è alta, più un calo dei ricavi pesa sul risultato.'),
    'Pareggio operativo': (
        '(Costi fissi + personale) / margine di contribuzione %.',
        'I ricavi annui che servono per avere EBITDA a zero.'),
    'Pareggio economico': (
        '(Costi fissi + personale + ammortamenti + oneri) / margine di contribuzione %.',
        'I ricavi annui che servono per non andare in perdita.'),
    'Pareggio di cassa': (
        '(Costi fissi + personale + rate + imposte) / margine di contribuzione %.',
        'I ricavi annui che servono per pagare tutto, rate comprese, senza contare gli ammortamenti.'),
    'Margine di sicurezza': (
        '(Ricavi − pareggio economico) / ricavi.',
        'Di quanto possono calare i ricavi prima di andare in perdita. Negativo vuol dire che si è già sotto il pareggio.'),
    'Leva operativa': (
        'Margine di contribuzione / EBIT.',
        'Quanto è sensibile il risultato ai ricavi: con leva 4, se i ricavi calano dell\'1% l\'EBIT cala del 4%.'),

    # --------------------------------------------------------------------- debito
    'Posizione finanziaria netta': (
        'Debiti finanziari registrati − liquidità dei conti attivi.',
        'Il debito vero, tolti i soldi disponibili. Negativa vuol dire più cassa che debiti.',
        'Dipende dalla liquidità: controlla saldi iniziali e conta di cassa.'),
    'PFN / EBITDA': (
        'Posizione finanziaria netta / EBITDA.',
        'In quanti anni di EBITDA si ripaga il debito netto. Le banche accettano di solito fino a 3 volte.'),
    'DSCR': (
        'Flusso al servizio del debito / rate annue (capitale + interessi).',
        'Quante volte il flusso di cassa copre le rate. Sotto 1 il flusso non basta: è un segnale d\'allerta del Codice della crisi.',
        'Senza finanziamenti registrati le rate sono stimate dagli ultimi 3 mesi di estratti.'),
    'Copertura degli interessi': ('EBITDA / interessi passivi.', 'Quante volte il margine operativo paga gli interessi.'),
    'Oneri finanziari sui ricavi': ('Interessi / ricavi.', 'Quanto pesa il costo del debito sulle vendite.'),
    'Debito finanziario': ('Residuo dei finanziamenti registrati alla fine del periodo.', 'Quanto resta da restituire a banche e società.'),

    # ------------------------------------------------------------------- liquidita'
    'Liquidità': (
        'Somma dei saldi dei conti attivi alla fine dell\'ultimo mese chiuso.',
        'I soldi disponibili.',
        'È giusta solo se i saldi iniziali sono corretti e la cassa è stata contata.'),
    'Liquidità oggi': (
        'Somma dei saldi dei conti attivi a oggi.',
        'I soldi disponibili.',
        'È giusta solo se i saldi iniziali sono corretti e la cassa è stata contata.'),
    'Giorni di cassa': (
        'Liquidità × 365 / costi operativi annui (ricavi − EBITDA).',
        'Quanti giorni si va avanti senza incassi. Sotto 30 è poco.'),
    'Liquidità sugli impegni a breve': (
        'Liquidità / (fornitori da pagare + rate dei 12 mesi).',
        'Se i soldi disponibili bastano per gli impegni vicini. Sopra 1,2 è tranquillo.'),
    'Conversione in cassa': (
        'Flusso operativo / EBITDA.',
        'Quanto dell\'EBITDA diventa davvero soldi in banca. Sopra il 100% vuol dire che entra più di quanto si guadagna, '
        'in genere perché mancano uscite o si pagano i fornitori più tardi.'),
    'Free cash flow': (
        'Flusso operativo + flusso investimenti, su base annua.',
        'La cassa libera per rate, titolare e riserve.'),
    'Giorni di pagamento fornitori (DPO)': (
        'Debiti verso fornitori aperti × 365 / acquisti annui IVA compresa.',
        'Dopo quanti giorni si pagano in media i fornitori.',
        'Le fatture pagate ma non abbinate al movimento risultano ancora aperte e lo alzano.'),
    'Fornitori scaduti': (
        'Somma delle scadenze passate e non ancora pagate.',
        'Quanto si è in ritardo con i fornitori.',
        'Comprende le fatture pagate ma non abbinate al movimento.'),

    # ---------------------------------------------------------------- KPI operativi
    'Ricavo medio al giorno': ('Ricavi del periodo / giorni del periodo.', 'Quanto incassa in media una giornata, IVA esclusa.'),
    'Lavaggi del servito al giorno': ('Lavaggi del periodo / giorni del periodo.', 'Il ritmo del servito.'),
    'Ricavo per lavaggio servito': (
        'Ricavi del servito / lavaggi.',
        'Lo scontrino medio del servito, IVA esclusa. Va confrontato con il costo pieno per lavaggio.'),
    'Ricavo per ora lavorata': (
        'Ricavi del servito / ore lavorate del servito.',
        'Quanto rende un\'ora di lavoro. Se è sotto il costo pieno orario, il servito non si paga.'),
    'Cicli self al giorno': ('Cicli dei portali / giorni del periodo.', 'Il ritmo dei portali. Le piste non sono comprese.'),
    'Ricavo per ciclo self': ('Ricavi dei portali / cicli dei portali.', 'Quanto rende in media un lavaggio al portale, IVA esclusa.'),
    'Produttività del personale': ('Ricavi / costo del personale.', 'Quanti euro di ricavo produce ogni euro di stipendio. Sopra 3 è buono.'),
}

# Stesso indicatore con un nome diverso in pagina
SPIEGAZIONI.update({
    # -------------------------------------------------------- previsione squadra
    'Squadra consigliata': (
        'Persone al servito più l\'eventuale persona al self.',
        'Quante persone mettere in turno. Si decide alle 19 del giorno prima, quando la previsione viene salvata.',
        'È un consiglio: eventi e picchi improvvisi il modello non li vede.'),
    'Lavaggi previsti': (
        'Stima fatta da giorno della settimana, festivi, stagione, lavaggi delle ultime settimane, prenotazioni e meteo previsto.',
        'Quanti lavaggi del servito ci si aspetta. "Fino a" aggiunge il margine ed è il numero usato per la squadra.'),
    'Cicli operatori': (
        'Cicli avviati dagli operatori ai portali, meno i lavaggi del servito. Non comprendono i contanti pagati al portale.',
        'Il lavoro del self che passa da una persona: è il numero confrontato con la soglia.',
        'Il "finora" di oggi può essere un po\' basso: il lavaggio del servito si conta quando si crea l\'ordine, prima che l\'auto passi dal portale.'),
    'Cicli self previsti': (
        'Contanti pagati al portale più cicli degli operatori, senza i lavaggi del servito.',
        'Quanto lavorano i portali per il self: serve per manutenzione e promozioni.',
        'La squadra non dipende da questo numero.'),
    'Probabilità di pioggia': (
        'La dà il servizio meteo: è il valore più alto fra le ore della giornata.',
        'Quanto è probabile che cada almeno un po\' di pioggia. 30% vuol dire che, in 10 giornate così, piove in circa 3.',
        '"Asciutto" significa che non sono previsti almeno 1 mm o 2 ore di pioggia. Con una probabilità alta, la pioggia è possibile ma poca.'),
    'Lavaggi per persona': (
        'Lo imposti tu.',
        'Quanti lavaggi del servito fa una persona in una giornata. Con 7, 20 lavaggi danno 3 persone.',
        'Troppo alto: squadra scarsa. Troppo basso: più turni del necessario.'),
    'Margine sui lavaggi': (
        'Percentuale aggiunta ai lavaggi previsti prima di calcolare la squadra.',
        'La riserva per i giorni più pieni del previsto.',
        'Più margine vuol dire più turni e meno giorni scoperti.'),
    'Soglia persona al self': (
        'Confronta i cicli operatori previsti con il numero che imposti.',
        'Da quel numero in su serve la persona al self. Sotto, il self lo segue il servito. Vuoto = sempre.',
        'Il singolo giorno è incerto: vicino alla soglia conviene decidere anche a occhio. A servito chiuso la persona resta.'),
    'Errore medio': (
        'Si sommano le differenze fra previsto e reale, senza segno, e si divide per il totale reale.',
        'Di quanto sbaglia di solito. Con 20% e 20 lavaggi al giorno, lo scarto tipico è di 4 lavaggi, in più o in meno. Più basso è meglio.',
        'Una parte è puro caso: "il caso da solo" dice sotto quale valore non si può scendere.'),
    'Giorni entro ±20%': (
        'Giorni in cui il reale è rimasto entro il 20% della previsione, diviso i giorni provati.',
        'Quanto spesso la previsione è stata buona. 70% vuol dire 7 giorni su 10.'),
    'Squadra giusta': (
        'Giorni in cui la squadra coincide con quella che sarebbe servita con i lavaggi reali, diviso i giorni provati.',
        'Quanto spesso il numero di persone era esatto.',
        'Gli altri giorni non pesano tutti uguale: una persona in più costa un turno, una in meno lascia scoperti.'),
    'Risparmio seguendo il modello': (
        'Turni al servito delle vostre scelte meno quelli del modello, per le ore di un turno e il costo orario medio.',
        'Quanto si sarebbe speso in meno seguendo il modello negli stessi giorni.',
        'Va letto insieme ai giorni scoperti, mostrati sotto: il risparmio vale se non ne aumentano troppi.'),
    'Ordini scoperti': (
        'Per ogni giorno, i lavaggi reali oltre quelli che la squadra riesce a fare (persone per lavaggi a persona). L\'incasso è quel numero per lo scontrino medio.',
        'Quanti ordini sarebbero stati a rischio con quella squadra: auto in coda, attese lunghe o clienti persi.',
        'È un massimo possibile, non una perdita certa: nei giorni pieni una squadra può fare più della media. L\'incasso è lordo: per confrontarlo col risparmio va guardato il margine, non tutto l\'importo.'),
    'Turni mancanti': (
        'Nei giorni scoperti, le persone che sarebbero servite in più. Si sommano su tutto il periodo.',
        'Quanto è grave essere scoperti: mancare di una persona in pochi giorni pesa meno che mancare di due spesso.',
        'Conviene confrontarli con i turni risparmiati: se sono vicini, il modello risparmia togliendo persone che servivano.'),
    'Scoperti': (
        'Giorni con meno persone di quelle che sarebbero servite, diviso i giorni provati.',
        'Quanto spesso la squadra sarebbe stata troppo piccola. Più basso è meglio.'),
    'Una o più in più': (
        'Giorni con almeno una persona oltre il necessario, diviso i giorni provati.',
        'Quanto spesso si è speso più del necessario.'),
    'Turni': (
        'Somma delle persone in tutti i giorni provati.',
        'Il costo del personale contato in giornate di lavoro: meno turni, meno spesa.'),
    'Errore sulla settimana': (
        'Differenza fra cicli previsti e reali sul totale di ogni settimana, diviso i cicli reali.',
        'Quanto è affidabile il totale settimanale. È più basso del giorno perché gli sbagli dei singoli giorni in parte si compensano.'),
    'Errore sul giorno': (
        'Come l\'errore medio, sui cicli di ogni giorno.',
        'Quanto sbaglia il singolo giorno. Il numero con ± è lo scarto tipico in cicli.',
        'Una parte è puro caso: "il caso da solo" dice sotto quale valore non si può scendere.'),
    'Totale previsto': (
        'Somma dei cicli previsti diviso somma dei cicli reali.',
        'Dice se il modello tende a stimare troppo o troppo poco. 100% è in pari, 110% è il 10% in più del reale.',
        'Guarda solo il totale: può stare a 100% anche con singoli giorni sbagliati.'),
    'Errore vero': (
        'Come l\'errore medio, ma sulle previsioni salvate davvero la sera prima.',
        'È il dato più sincero: non è una prova, è quello che è successo.',
        'Diventa affidabile dopo alcune settimane di previsioni salvate.'),
    'Cosa conta di più': (
        'Per ogni situazione si confronta la previsione con e senza, lasciando uguale tutto il resto, e si fa la media sullo storico.',
        '-20% vuol dire un quinto dei lavaggi in meno, +20% un quinto in più.',
        'Sono medie del passato: un singolo giorno può andare diversamente.'),
    'Self per fascia oraria': (
        'Media dei cicli per ora. A destra i cicli medi di ogni fascia e la loro percentuale sulla giornata.',
        'In quali ore il self lavora di più: più il colore è scuro, più cicli ci sono.',
        'Sono dati storici: non entrano nel calcolo della squadra.'),
    'Pioggia vista': (
        'Giorni di pioggia che il servizio aveva previsto, diviso i giorni di pioggia.',
        'Quante piogge ha visto arrivare. Più alto è meglio.'),
    'Falsi allarmi': (
        'Giorni asciutti in cui prevedeva pioggia, diviso i giorni asciutti.',
        'Quante volte ha dato pioggia a vuoto. Più basso è meglio.'),
    'Giorni coperti': (
        'Giorni in cui il dato viene davvero da quel servizio, diviso i giorni provati.',
        'Per gli altri giorni si usa la previsione automatica: sotto una certa quota il confronto dice poco.'),
    'Turni risparmiati': (
        'Turni al servito delle vostre scelte meno quelli del modello con quel servizio.',
        'Quante giornate di lavoro in meno, negli stessi giorni.'),
})

ALIAS = {
    'EBITDA (margine operativo lordo)': 'EBITDA',
    'Errore lavaggi': 'Errore medio',
    'EBIT (risultato operativo)': 'EBIT',
    'Ricavi': 'Ricavi netti',
}


def spiegazione(nome):
    return SPIEGAZIONI.get(ALIAS.get(nome, nome))
