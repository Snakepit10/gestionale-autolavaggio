"""Le caselle di informazioni degli indicatori (tag {% info %} delle pagine
di contabilita'): nome come appare in pagina -> come si calcola, cosa
indica e, se serve, a cosa fare attenzione."""

SPIEGAZIONI = {
    # ---------------------------------------------------------- conto economico
    'Ricavi netti': (
        'In base a quanto scelto in alto. Rilevato reale: servito e prodotti = totale ordini meno l\'IVA del registratore; cambia gettoni e monete online = fatturato diviso 1,22; i portali sono il resto dei soldi delle quadrature meno l\'IVA delle loro chiusure di cassa. '
        'Fatturato per canale: ordini del servito, prodotti e casse automatiche, diviso 1,22.',
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
    'Risultato del centro': (
        'Ricavi del centro − i costi attribuiti al centro (variabili, fissi, personale, ammortamenti).',
        'Quanto guadagna ogni attività prima di dividere i costi comuni. La colonna Comune raccoglie i costi non attribuiti.'),
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
        'Margine diretto − quota dei costi comuni.',
        'L\'utile vero del servito, dopo aver pagato anche energia, acqua, assicurazioni e tutta la struttura per la sua parte.'),
    'Cicli portali': (
        'Cicli scritti nelle chiusure dei due portali nel periodo, meno i lavaggi completi degli ordini del servito '
        '(che passano anche loro dal portale). Sotto il valore si vedono i cicli totali e quelli del servito.',
        'Quanti lavaggi hanno fatto i clienti self ai portali a spazzole.',
        'Le piste non contano. I giorni senza chiusura della cassa non entrano nel conteggio. Per il servito contano '
        'i servizi con "lavaggio completo" nel nome, come nel report periodo.'),
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
        'Ordini del periodo non annullati, escluse le vendite di soli prodotti. Ogni ordine conta 1, anche con più servizi.',
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
ALIAS = {
    'EBITDA (margine operativo lordo)': 'EBITDA',
    'EBIT (risultato operativo)': 'EBIT',
    'Ricavi': 'Ricavi netti',
}


def spiegazione(nome):
    return SPIEGAZIONI.get(ALIAS.get(nome, nome))
