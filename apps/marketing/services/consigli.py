"""Motore consigli della dashboard marketing.

Regole deterministiche sui dati gia' calcolati (segmentazione,
statistiche campagne): ogni regola guarda una condizione, e se scatta
produce una card con un'azione concreta (CTA) per aumentare le
vendite. Nessuna chiamata esterna, nessun costo: tutto in-process.

Ogni regola privata `_regola_*(ctx)` ritorna un dict
{icona, titolo, testo, cta_url, cta_label, priorita} oppure None.
Le regole hanno soppressioni per non consigliare cose gia' fatte
(es. "contatta i dormienti" mentre una campagna sui dormienti e' gia'
in corso).

Seam AI (inerte): `analisi_ai(raccogli_contesto())` e' gia' cablata
nella vista e oggi ritorna []. Quando si decidera' di attivare
l'analisi AI (Claude API), basta implementare `analisi_ai` facendole
restituire card nello stesso formato — vista e template non cambiano.
Vincolo gia' concordato: le eventuali campagne proposte dall'AI
dovranno usare SOLO template Meta approvati.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.marketing.models import Campagna, SegmentoPersonalizzato


def _campagne_attive():
    return Campagna.objects.filter(stato__in=('in_coda', 'in_corso', 'in_pausa'))


def _attiva_su_segmento(chiave: str) -> bool:
    """True se c'e' gia' una campagna attiva che include quel segmento."""
    return any(
        chiave in (c.segmento_origine or '').split(',')
        for c in _campagne_attive()
    )


def _regola_richiamo_spento(ctx):
    """[100] Il richiamo automatico e' la rete di sicurezza: se e'
    spento o senza template, nessun cliente viene ricontattato da solo."""
    cfg = ctx['cfg']
    if cfg.richiamo_automatico_attivo and cfg.richiamo_template_meta:
        return None
    if not cfg.richiamo_automatico_attivo:
        testo = (f'Il richiamo automatico e\' spento: i clienti spariti da '
                 f'{cfg.richiamo_giorni_dopo} giorni non ricevono nessun '
                 f'promemoria. E\' la campagna che lavora da sola, senza '
                 f'muovere un dito.')
    else:
        testo = ('L\'interruttore del richiamo automatico e\' ON ma manca '
                 'il template Meta: i promemoria NON partono. Inserisci il '
                 'nome del template approvato nelle impostazioni.')
    return {
        'icona': 'bi-alarm', 'priorita': 100,
        'titolo': 'Accendi il richiamo automatico',
        'testo': testo,
        'cta_url': reverse('marketing:impostazioni'),
        'cta_label': 'Vai alle impostazioni',
    }


def _regola_dormienti(ctx):
    """[90] Clienti dormienti = fatturato che gia' conoscevi e che si
    sta perdendo. Il valore storico rende il consiglio tangibile."""
    dormienti = ctx['ris'].dormienti
    if len(dormienti) < 5 or _attiva_su_segmento('dormienti'):
        return None
    spesa = sum(float(cs.totale_speso) for cs in dormienti)
    return {
        'icona': 'bi-moon', 'priorita': 90,
        'titolo': f'Riattiva {len(dormienti)} clienti dormienti',
        'testo': (f'{len(dormienti)} clienti non lavano da oltre '
                  f'{ctx["cfg"].giorni_dormiente} giorni: in passato hanno '
                  f'speso {spesa:.0f} euro in totale. Una campagna con uno '
                  f'sconto di rientro e\' il modo piu\' rapido di '
                  f'recuperare fatturato.'),
        'cta_url': reverse('marketing:campagna-nuova') + '?segmenti=dormienti',
        'cta_label': 'Crea campagna dormienti',
    }


def _regola_big_spender_fermi(ctx):
    """[80] Pochi clienti che valgono molto: trattamento dedicato."""
    fermi = [cs for cs in ctx['stats']
             if float(cs.totale_speso) >= 200 and cs.giorni_da_ultimo > 60]
    if len(fermi) < 3:
        return None
    # Se esiste gia' un segmento custom attivo basato sulla spesa
    # totale, l'operatore ha gia' recepito l'idea: non ripetersi.
    if SegmentoPersonalizzato.objects.filter(
            attivo=True, spesa_totale_min__isnull=False).exists():
        return None
    spesa = sum(float(cs.totale_speso) for cs in fermi)
    return {
        'icona': 'bi-gem', 'priorita': 80,
        'titolo': f'{len(fermi)} clienti top fermi da oltre 60 giorni',
        'testo': (f'Hanno speso almeno 200 euro a testa ({spesa:.0f} euro '
                  f'in totale) ma non si vedono da piu\' di 2 mesi. Vale '
                  f'un messaggio dedicato: crea un segmento "big spender" '
                  f'e riservagli un\'offerta speciale.'),
        'cta_url': (reverse('marketing:segmento-custom-nuovo') +
                    '?spesa_totale_min=200&giorni_ultimo_min=60'),
        'cta_label': 'Crea segmento big spender',
    }


def _regola_one_shot_recenti(ctx):
    """[70] Chi ha provato una volta di recente e' il piu' facile da
    convertire in abituale: il ricordo del servizio e' fresco."""
    recenti = [cs for cs in ctx['ris'].one_shot if cs.giorni_da_ultimo <= 45]
    if len(recenti) < 3 or _attiva_su_segmento('one_shot'):
        return None
    return {
        'icona': 'bi-person-plus', 'priorita': 70,
        'titolo': f'Fai tornare {len(recenti)} clienti nuovi',
        'testo': (f'{len(recenti)} clienti hanno fatto il primo (e unico) '
                  f'lavaggio negli ultimi 45 giorni. Un messaggio di '
                  f'benvenuto con un piccolo incentivo sul secondo '
                  f'lavaggio e\' il momento giusto per fidelizzarli.'),
        'cta_url': reverse('marketing:campagna-nuova') + '?segmenti=one_shot',
        'cta_label': 'Crea campagna one-shot',
    }


def _regola_miglior_segmento(ctx):
    """[60] Raddoppia su cio' che funziona: se un segmento converte
    bene, replicare la campagna e' la mossa a rischio piu' basso."""
    from .statistiche import statistiche_per_segmento
    candidati = [r for r in statistiche_per_segmento()
                 if r['n_inviati'] >= 10 and r['tasso_conversione'] >= 15]
    if not candidati:
        return None
    best = candidati[0]  # gia' ordinati per tasso decrescente
    ultima = (Campagna.objects
              .filter(segmento_origine=best['segmento'])
              .exclude(stato='bozza')
              .order_by('-creata_il').first())
    if ultima is None or _attiva_su_segmento(best['segmento'].split(',')[0]):
        return None
    return {
        'icona': 'bi-trophy', 'priorita': 60,
        'titolo': f'"{best["label"]}" converte al {best["tasso_conversione"]:.0f}%',
        'testo': (f'Le campagne su questo segmento hanno gia'
                  f' portato {best["n_conversioni"]} ritorni e '
                  f'{best["fatturato"]:.0f} euro. Duplica l\'ultima '
                  f'campagna ("{ultima.nome}") e rilanciala.'),
        'cta_url': (reverse('marketing:campagna-nuova') +
                    f'?duplica={ultima.pk}'),
        'cta_label': 'Duplica campagna vincente',
    }


def _regola_lettura_bassa(ctx):
    """[50] Un template che non viene letto spreca il tetto giornaliero
    di invii: meglio cambiarlo prima di continuare a spendere invii."""
    cutoff = timezone.now() - timedelta(days=90)
    from .statistiche import statistiche_campagna
    peggiore = None
    for c in Campagna.objects.filter(creata_il__gte=cutoff).exclude(stato='bozza'):
        stats = statistiche_campagna(c)
        if stats['n_inviati'] < 10 or stats['tasso_lettura'] >= 40:
            continue
        if peggiore is None or stats['tasso_lettura'] < peggiore[1]['tasso_lettura']:
            peggiore = (c, stats)
    if peggiore is None:
        return None
    c, stats = peggiore
    return {
        'icona': 'bi-envelope-open', 'priorita': 50,
        'titolo': f'Solo il {stats["tasso_lettura"]:.0f}% legge "{c.nome}"',
        'testo': (f'Su {stats["n_inviati"]} messaggi inviati, pochi vengono '
                  f'letti. Prova un template piu\' personale (nome del '
                  f'cliente nel testo) o sposta la fascia oraria di invio '
                  f'nelle impostazioni. Nota: chi ha le conferme di lettura '
                  f'disattivate non viene contato, il dato reale e\' un po\' '
                  f'piu\' alto.'),
        'cta_url': reverse('marketing:campagna-dettaglio', args=[c.pk]),
        'cta_label': 'Vedi la campagna',
    }


def _regola_telefoni_mancanti(ctx):
    """[40] Ogni cliente senza telefono e' fuori da qualsiasi campagna."""
    from apps.clienti.models import Cliente
    n = Cliente.objects.filter(telefono='').count()
    if n < 10:
        return None
    return {
        'icona': 'bi-telephone-x', 'priorita': 40,
        'titolo': f'{n} clienti senza numero di telefono',
        'testo': (f'{n} anagrafiche non hanno un numero: nessuna campagna '
                  f'li puo\' raggiungere. Alla prossima visita in cassa '
                  f'chiedi il numero, o ripulisci le schede inutilizzate.'),
        'cta_url': reverse('clienti:pulizia'),
        'cta_label': 'Pulizia anagrafica',
    }


def _regola_nessuna_campagna(ctx):
    """[30] Il marketing funziona se e' costante: un mese di silenzio
    e' un mese in cui i clienti si dimenticano di te."""
    if _campagne_attive().exists():
        return None
    cutoff = timezone.now() - timedelta(days=30)
    if Campagna.objects.filter(creata_il__gte=cutoff).exists():
        return None
    return {
        'icona': 'bi-send', 'priorita': 30,
        'titolo': 'Nessuna campagna da 30 giorni',
        'testo': ('Non ci sono campagne attive e l\'ultima e\' stata '
                  'creata piu\' di un mese fa. Anche un semplice '
                  'promemoria stagionale (pioggia, pollini, sale '
                  'invernale) tiene vivo il rapporto coi clienti.'),
        'cta_url': reverse('marketing:campagna-nuova'),
        'cta_label': 'Crea una campagna',
    }


_REGOLE = (
    _regola_richiamo_spento,
    _regola_dormienti,
    _regola_big_spender_fermi,
    _regola_one_shot_recenti,
    _regola_miglior_segmento,
    _regola_lettura_bassa,
    _regola_telefoni_mancanti,
    _regola_nessuna_campagna,
)


def genera_consigli(stats, ris, cfg) -> list[dict]:
    """Valuta tutte le regole e ritorna le card ordinate per priorita'.

    stats = statistiche_clienti() gia' calcolate (riusate, non
    ricalcolate); ris = segmenta_clienti(stats=stats); cfg = singleton
    ImpostazioniMarketing.
    """
    ctx = {'stats': stats, 'ris': ris, 'cfg': cfg}
    out = []
    for regola in _REGOLE:
        card = regola(ctx)
        if card is not None:
            out.append(card)
    out.sort(key=lambda c: -c['priorita'])
    return out


# ---------------------------------------------------------------------
# Consulente AI (la generazione vera e' in services/ai.py)
# ---------------------------------------------------------------------

def analisi_ai(contesto: dict | None = None) -> list[dict]:
    """Card del consulente AI in dashboard.

    NON chiama la Claude API (costerebbe a ogni caricamento pagina):
    mostra una card se l'ultima analisi salvata e' recente e ha
    proposte, rimandando alla pagina del consulente. La generazione
    vera avviene solo on-demand da quella pagina (services/ai.py).
    """
    from apps.marketing.models import AnalisiAI

    ultima = AnalisiAI.objects.first()   # ordering -creata_il
    if ultima is None or ultima.n_proposte == 0:
        return []
    if (timezone.now() - ultima.creata_il).days > 30:
        return []
    return [{
        'icona': 'bi-stars', 'priorita': 85,
        'titolo': f'Il consulente AI ha {ultima.n_proposte} proposte pronte',
        'testo': (f'Analisi del {timezone.localtime(ultima.creata_il):%d/%m/%Y}: '
                  f'{len(ultima.proposte_campagne)} campagne, '
                  f'{len(ultima.proposte_segmenti)} segmenti e '
                  f'{len(ultima.promozioni)} promozioni da rivedere. '
                  f'Nulla parte senza la tua approvazione.'),
        'cta_url': reverse('marketing:ai'),
        'cta_label': 'Apri il consulente AI',
    }]
