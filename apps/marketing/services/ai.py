"""Consulente AI marketing: analisi dati e proposte via Claude API.

Flusso: l'operatore preme "Genera analisi" -> si raccoglie un contesto
di SOLI aggregati anonimi (mai nomi/telefoni dei clienti) -> Claude
risponde in JSON strutturato (schema imposto dall'API) -> l'analisi
viene salvata in AnalisiAI e mostrata nella pagina AI.

Le proposte diventano operative SOLO con un click dell'operatore:
- proposta segmento  -> crea un SegmentoPersonalizzato
- proposta campagna  -> apre il composer precompilato (poi il solito
  flusso preview -> conferma: e' li' l'autorizzazione all'invio)
- proposta template  -> bozza pronta da incollare in Meta WhatsApp
  Manager (l'invio reale resta possibile solo dopo l'approvazione
  Meta: se la campagna usa un template non approvato, l'invio fallisce
  e la UI lo segnala col badge sullo stato del template)
Il contesto include anche listino servizi e aggregati di vendita
(fatturato, top servizi, giorni deboli) per analisi piu' profonde.

Costi: una analisi consuma qualche migliaio di token (centesimi).
I token di ogni analisi sono salvati per trasparenza.
"""
import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Criteri numerici ammessi nelle proposte di segmento (stessi campi
# del modello SegmentoPersonalizzato).
CRITERI_SEGMENTO = (
    'lavaggi_min', 'lavaggi_max', 'giorni_ultimo_min', 'giorni_ultimo_max',
    'frequenza_min', 'frequenza_max', 'spesa_totale_min', 'spesa_totale_max',
    'spesa_media_min', 'spesa_media_max',
)


def ai_disponibile() -> bool:
    """True se la chiave API e' configurata."""
    return bool(settings.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------
# Template Meta approvati
# ---------------------------------------------------------------------

def template_approvati() -> list[dict]:
    """Lista dei template WhatsApp APPROVATI su Meta (nome + testo body).

    Cache 1 ora: la lista cambia di rado e la pagina AI la mostra a
    ogni caricamento. Ritorna [] se WABA non configurato o errore API
    (in quel caso l'AI ripiega sui template gia' usati in passato).
    """
    waba_id = getattr(settings, 'META_WHATSAPP_BUSINESS_ACCOUNT_ID', '')
    if not waba_id or not settings.META_WHATSAPP_ACCESS_TOKEN:
        return []

    cache_key = 'wa_templates_approvati'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    out = []
    try:
        url = (f'https://graph.facebook.com/'
               f'{settings.META_WHATSAPP_API_VERSION}/{waba_id}/message_templates')
        r = requests.get(
            url,
            params={'fields': 'name,status,category,components,language',
                    'limit': 100},
            headers={'Authorization':
                     f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}'},
            timeout=15,
        )
        if r.status_code >= 400:
            logger.warning('listing template Meta fallito (%s): %s',
                           r.status_code, r.text[:200])
            return []
        for tpl in r.json().get('data', []):
            if tpl.get('status') != 'APPROVED':
                continue
            body = ''
            for comp in tpl.get('components', []):
                if comp.get('type') == 'BODY':
                    body = comp.get('text', '')
                    break
            out.append({
                'nome': tpl.get('name', ''),
                'categoria': tpl.get('category', ''),
                'lingua': tpl.get('language', ''),
                'testo': body,
            })
    except requests.RequestException as e:
        logger.warning('listing template Meta errore: %s', e)
        return []

    cache.set(cache_key, out, 60 * 60)
    return out


# ---------------------------------------------------------------------
# Listino e vendite (per analisi su prezzi e andamento)
# ---------------------------------------------------------------------

def listino_servizi() -> list[dict]:
    """Catalogo servizi/prodotti attivi con prezzo, per proposte con
    numeri realistici (sconti sostenibili, bundle sensati)."""
    from apps.core.models import ServizioProdotto

    return [
        {
            'titolo': s.titolo,
            'tipo': s.tipo,
            'prezzo': float(s.prezzo),
            'categoria': s.categoria.nome if s.categoria_id else '',
            'durata_minuti': s.durata_minuti if s.tipo == 'servizio' else None,
        }
        for s in (ServizioProdotto.objects.filter(attivo=True)
                  .select_related('categoria').order_by('categoria__nome', 'titolo'))
    ]


def report_vendite() -> dict:
    """Aggregati di vendita (stessa base dei report giornata/periodo di
    /finanze/): fatturato, scontrino medio, top servizi, giorni deboli.
    Solo numeri aggregati, nessun dato del singolo cliente."""
    from datetime import timedelta

    from django.db.models import Count, Sum
    from django.utils import timezone as tz

    from apps.ordini.models import ItemOrdine, Ordine

    oggi = tz.localtime(tz.now()).date()

    def _fatturato_ordini(da, a):
        qs = Ordine.objects.filter(
            data_ora__date__gte=da, data_ora__date__lte=a,
        ).exclude(stato='annullato')
        agg = qs.aggregate(tot=Sum('totale_finale'), n=Count('id'))
        tot = float(agg['tot'] or 0)
        n = agg['n'] or 0
        return {'fatturato': round(tot, 2), 'n_ordini': n,
                'scontrino_medio': round(tot / n, 2) if n else 0.0}

    ultimi_30 = _fatturato_ordini(oggi - timedelta(days=29), oggi)
    prec_30 = _fatturato_ordini(oggi - timedelta(days=59),
                                oggi - timedelta(days=30))

    # Vendite self-service (portali/cambia gettoni) dalle chiusure
    # automatiche: contesto utile ma non indispensabile, quindi non
    # deve mai far fallire l'analisi.
    self_service_30 = None
    try:
        from apps.finanze.models import ChiusuraCassaAutomatica
        tot = 0.0
        for c in (ChiusuraCassaAutomatica.objects
                  .filter(data__gte=oggi - timedelta(days=29), data__lte=oggi)
                  .select_related('cassa')):
            if not c.cassa.modalita_registratore:
                tot += float(c.vendita_totale)
        self_service_30 = round(tot, 2)
    except Exception:
        pass

    # Top servizi per ricavo negli ultimi 90 giorni
    from django.db.models import DecimalField, ExpressionWrapper, F
    top = (
        ItemOrdine.objects
        .filter(ordine__data_ora__date__gte=oggi - timedelta(days=89))
        .exclude(ordine__stato='annullato')
        .values('servizio_prodotto__titolo')
        .annotate(
            ricavo=Sum(ExpressionWrapper(
                F('prezzo_unitario') * F('quantita'),
                output_field=DecimalField(max_digits=12, decimal_places=2))),
            qta=Sum('quantita'),
        )
        .order_by('-ricavo')[:12]
    )
    top_servizi = [
        {'servizio': r['servizio_prodotto__titolo'],
         'ricavo': float(r['ricavo'] or 0), 'quantita': r['qta'] or 0}
        for r in top
    ]

    # Fatturato per giorno della settimana (90gg): trova i giorni deboli
    per_giorno = {i: 0.0 for i in range(7)}
    for o in (Ordine.objects
              .filter(data_ora__date__gte=oggi - timedelta(days=89))
              .exclude(stato='annullato')
              .values_list('data_ora', 'totale_finale')):
        per_giorno[tz.localtime(o[0]).weekday()] += float(o[1] or 0)
    giorni_it = ['lunedi', 'martedi', 'mercoledi', 'giovedi',
                 'venerdi', 'sabato', 'domenica']
    fatturato_settimana = {giorni_it[i]: round(per_giorno[i], 2)
                           for i in range(7)}

    return {
        'ordini_ultimi_30gg': ultimi_30,
        'ordini_30gg_precedenti': prec_30,
        'self_service_ultimi_30gg': self_service_30,
        'top_servizi_90gg': top_servizi,
        'fatturato_per_giorno_settimana_90gg': fatturato_settimana,
    }


# ---------------------------------------------------------------------
# Contesto (solo aggregati, zero dati personali)
# ---------------------------------------------------------------------

def contesto_per_ai() -> dict:
    """Fotografia aggregata del marketing da passare al modello."""
    from apps.marketing.models import ImpostazioniMarketing, SegmentoPersonalizzato
    from .campagne import PLACEHOLDER_SUPPORTATI
    from .segmentazione import (SEGMENTI_LABEL, filtra_segmento_personalizzato,
                                segmenta_clienti, statistiche_clienti)
    from .statistiche import (kpi_dashboard, serie_mensile,
                              stats_ultime_campagne, statistiche_per_segmento)

    from django.utils import timezone as tz

    cfg = ImpostazioniMarketing.get_solo()
    stats = statistiche_clienti()
    ris = segmenta_clienti(stats=stats)
    templates = template_approvati()

    # Top clienti in forma ANONIMA (scelta esplicita dell'utente):
    # l'AI puo' ragionare su valore e rischio abbandono dei clienti
    # migliori senza che nomi o telefoni escano dal CRM.
    top_anonimi = [
        {
            'spesa_totale': float(cs.totale_speso),
            'spesa_media': float(cs.spesa_media),
            'n_lavaggi': cs.totale_lavaggi,
            'giorni_da_ultimo': cs.giorni_da_ultimo,
            'frequenza_media_giorni': round(cs.frequenza_media_giorni, 1)
            if cs.frequenza_media_giorni else None,
        }
        for cs in sorted(stats, key=lambda c: -c.totale_speso)[:15]
    ]

    # Tasso di ritorno aggregato: tra chi e' tornato almeno una volta,
    # quanti hanno un ritmo entro 30/60/90 giorni.
    abituali = [cs for cs in stats if cs.frequenza_media_giorni is not None]
    def _pct_entro(giorni):
        if not abituali:
            return 0.0
        n = sum(1 for cs in abituali if cs.frequenza_media_giorni <= giorni)
        return round(n / len(abituali) * 100, 1)

    oggi = tz.localtime(tz.now()).date()
    return {
        'attivita': {
            'nome': 'Autolavaggio MasterWash',
            'luogo': 'Licata (AG), Via Palma 302',
            'canale_marketing': 'campagne WhatsApp con template Meta',
        },
        'data_odierna': oggi.isoformat(),
        'mese_corrente': ('gennaio febbraio marzo aprile maggio giugno luglio '
                          'agosto settembre ottobre novembre dicembre'
                          ).split()[oggi.month - 1],
        'kpi': kpi_dashboard(),
        'segmenti_automatici': {
            chiave: {'label': SEGMENTI_LABEL[chiave], 'n_clienti': len(ris.get(chiave))}
            for chiave in ('attivi', 'rallentamento', 'dormienti', 'one_shot')
        },
        'segmenti_personalizzati': [
            {'chiave': s.chiave, 'nome': s.nome,
             'criteri': s.criteri_leggibili(),
             'n_clienti': len(filtra_segmento_personalizzato(s, stats))}
            for s in SegmentoPersonalizzato.objects.filter(attivo=True)
        ],
        'serie_mensile': serie_mensile(6),
        'ultime_campagne': stats_ultime_campagne(8),
        'rendimento_per_segmento': statistiche_per_segmento(),
        'impostazioni': {
            'max_invii_giorno': cfg.max_invii_giorno,
            'fascia_invio': f'{cfg.orario_invio_da:%H:%M}-{cfg.orario_invio_a:%H:%M}',
            'finestra_no_ricontatto_giorni': cfg.finestra_no_ricontatto_giorni,
            'finestra_conversione_giorni': cfg.giorni_finestra_conversione,
            'richiamo_automatico': {
                'attivo': cfg.richiamo_automatico_attivo,
                'giorni_dopo': cfg.richiamo_giorni_dopo,
                'template': cfg.richiamo_template_meta,
            },
        },
        'template_meta_approvati': templates,
        'placeholder_supportati': list(PLACEHOLDER_SUPPORTATI),
        'listino': listino_servizi(),
        'vendite': report_vendite(),
        'top_clienti_anonimi': top_anonimi,
        'tasso_ritorno': {
            'nota': 'percentuale dei clienti abituali (>=2 lavaggi) che '
                    'torna in media entro N giorni',
            'entro_30gg': _pct_entro(30),
            'entro_60gg': _pct_entro(60),
            'entro_90gg': _pct_entro(90),
        },
    }


# ---------------------------------------------------------------------
# Chiamata Claude
# ---------------------------------------------------------------------

# Nota schema: additionalProperties False ovunque, i criteri numerici
# dei segmenti sono nullable (l'AI compila solo quelli che servono).
_SCHEMA_ANALISI = {
    'type': 'object',
    'properties': {
        'analisi': {
            'type': 'string',
            'description': 'Analisi discorsiva (in italiano) dello stato del '
                           'marketing: cosa va bene, cosa no, dove sono le '
                           'opportunita. 2-4 paragrafi.',
        },
        'best_practice': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'Best practice concrete e applicabili a un '
                           'autolavaggio con marketing WhatsApp.',
        },
        'proposte_segmenti': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'nome': {'type': 'string'},
                    'descrizione': {'type': 'string'},
                    'motivazione': {'type': 'string'},
                    **{c: {'type': ['number', 'null']} for c in CRITERI_SEGMENTO},
                },
                'required': ['nome', 'descrizione', 'motivazione',
                             *CRITERI_SEGMENTO],
                'additionalProperties': False,
            },
        },
        'proposte_campagne': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'nome': {'type': 'string'},
                    'segmenti': {
                        'type': 'array', 'items': {'type': 'string'},
                        'description': 'Chiavi segmento ESISTENTI (attivi, '
                                       'rallentamento, dormienti, one_shot, '
                                       'tutti, custom:<id>).',
                    },
                    'template_meta': {
                        'type': 'string',
                        'description': 'Nome di un template approvato dal '
                                       'contesto, OPPURE il nome di un '
                                       'template proposto in '
                                       'proposte_template di questa analisi.',
                    },
                    'template_params': {
                        'type': 'array', 'items': {'type': 'string'},
                        'description': 'Parametri body nell ordine {{1}},{{2}}: '
                                       'placeholder supportati o testo fisso.',
                    },
                    'promozione': {'type': 'string'},
                    'motivazione': {'type': 'string'},
                },
                'required': ['nome', 'segmenti', 'template_meta',
                             'template_params', 'promozione', 'motivazione'],
                'additionalProperties': False,
            },
        },
        'proposte_template': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'nome': {
                        'type': 'string',
                        'description': 'Nome template stile Meta: minuscolo, '
                                       'parole separate da underscore.',
                    },
                    'categoria': {'type': 'string',
                                  'enum': ['MARKETING', 'UTILITY']},
                    'testo_body': {
                        'type': 'string',
                        'description': 'Testo del messaggio con segnaposto '
                                       '{{1}}, {{2}}... pronto da incollare '
                                       'in Meta WhatsApp Manager.',
                    },
                    'esempio_parametri': {
                        'type': 'array', 'items': {'type': 'string'},
                        'description': 'Un valore di esempio per ogni '
                                       'segnaposto, nell ordine.',
                    },
                    'motivazione': {'type': 'string'},
                },
                'required': ['nome', 'categoria', 'testo_body',
                             'esempio_parametri', 'motivazione'],
                'additionalProperties': False,
            },
        },
        'promozioni': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'titolo': {'type': 'string'},
                    'descrizione': {'type': 'string'},
                    'come_attuarla': {'type': 'string'},
                },
                'required': ['titolo', 'descrizione', 'come_attuarla'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['analisi', 'best_practice', 'proposte_segmenti',
                 'proposte_campagne', 'proposte_template', 'promozioni'],
    'additionalProperties': False,
}

_SYSTEM_PROMPT = """# RUOLO

Sei il consulente marketing e analista dati di Autolavaggio MasterWash \
(Licata, AG). Lavori dentro il loro CRM su DATI AGGREGATI E ANONIMI: mai \
nomi, telefoni o conversazioni WhatsApp dei clienti (non richiederli mai). \
Obiettivo: aumentare la frequenza di ritorno, il ticket medio e recuperare \
gli inattivi con analisi, segmentazione e campagne WhatsApp conformi Meta.

# CONTESTO AZIENDALE

- Autolavaggio B2C locale in Sicilia; canale marketing: WhatsApp (template \
Meta). Ha anche un sistema di monete virtuali/gettoni per i lavaggi \
self-service e prenotazioni online.
- Business stagionale: picchi con pioggia/polvere sahariana, pre-festivita', \
cambio stagione; usa data_odierna e mese_corrente del contesto per proposte \
a tema meteo/stagione.

# SEGMENTAZIONE (RFM adattato)

Ragiona per archetipi RFM su Recency/Frequency/Monetary e, quando utile, \
proponili in proposte_segmenti con i criteri numerici disponibili \
(lavaggi_min/max, giorni_ultimo_min/max, frequenza_min/max, \
spesa_totale_min/max, spesa_media_min/max):
- VIP/Abituali: alta frequenza e alta spesa -> fidelizzare, upselling premium
- Regolari: un lavaggio ogni 3-6 settimane -> aumentare frequenza, abbonamento
- Occasionali: ogni 2-3 mesi -> promo mirate per accorciare il ciclo
- In raffreddamento: fermi da 45-90 giorni -> riattivazione soft
- Dormienti: fermi oltre 90-120 giorni -> win-back con offerta forte
- Nuovi: primo lavaggio recente -> portarli al secondo lavaggio entro 3 settimane
Ricalibra le soglie sui dati reali del contesto (tasso_ritorno, frequenze) \
e spiega perche'. Non duplicare segmenti gia' esistenti. Compila solo i \
criteri utili, lascia null gli altri. Filtri per tipo di servizio acquistato \
NON esistono: se servirebbero, dillo nell'analisi senza proporre il segmento.

# ANALISI (campo 'analisi')

Copri sempre, coi numeri del contesto: andamento vendite vs periodo \
precedente e ticket medio; mix servizi (top_servizi, cosa spingere); tasso \
di ritorno; top_clienti_anonimi ad alto valore fermi da troppo (rischio \
abbandono); giorni della settimana deboli. Chiudi con le 2-3 azioni piu' \
importanti in ordine di priorita' con impatto stimato. Distingui sempre \
cio' che i dati mostrano dalle tue ipotesi; se un dato manca, dillo e \
proponi come raccoglierlo. Non inventare mai numeri.

# PROMOZIONI

Solo promozioni giustificate dai dati, con target, offerta, durata e come \
misurarne il successo in come_attuarla. Leve adatte: tessera fedelta' (10o \
lavaggio omaggio), promo meteo ("dopo la pioggia, torna entro 48h"), \
upselling stagionale (igienizzazione in primavera, trattamenti invernali, \
lucidatura pre-estate), win-back a scaglioni (sconto crescente con \
l'inattivita'), promo sui giorni fiacchi individuati dai dati, porta un \
amico, abbonamento mensile, gettoni omaggio del sistema monete. Calibra gli \
sconti sul listino reale: mai sconti generalizzati a tutta la lista \
(erodono margine su chi tornerebbe comunque).

# CAMPAGNE E TEMPLATE WHATSAPP

- Le campagne proposte usano un template di template_meta_approvati OPPURE \
un template che proponi in proposte_template (stesso nome): l'operatore lo \
creera' su Meta WhatsApp Manager e attendera' l'approvazione. Preferisci i \
template gia' approvati quando adatti.
- I segmenti delle campagne devono essere chiavi esistenti del contesto \
(attivi, rallentamento, dormienti, one_shot, tutti, custom:<id>); un \
segmento appena proposto NON e' ancora usabile in campagna.
- template_params: placeholder supportati ({nome}, {giorni_ultimo_lavaggio}, \
{totale_lavaggi}) o testo fisso, nell'ordine dei segnaposto del template.
- Regole per i testi in proposte_template: nome tecnico snake_case; \
categoria MARKETING per promo, UTILITY solo per messaggi legati a una \
transazione; variabili {{1}}, {{2}}... MAI adiacenti e MAI a inizio o fine \
messaggio; punta a 300-500 caratteri (max 1024); tono amichevole, diretto, \
locale, mai aggressivo; UNA sola call to action; 1-3 emoji al massimo; \
niente prezzi ambigui o promesse ingannevoli; nei promozionali chiudi \
sempre con "Rispondi STOP per non ricevere piu' promozioni".
- Frequenza: massimo 2-3 messaggi promozionali al mese per cliente. \
Rispetta i vincoli del contesto (tetto giornaliero, fascia oraria, \
finestra no-ricontatto): non proporre volumi impossibili.

Esempi di stile per i template (adattali ai dati, non copiarli a forza):
- winback_60_giorni [MARKETING]: "Ciao {{1}}! E' un po' che non vediamo la \
tua auto e ci manca! Torna entro il {{2}} e per te c'e' il 10% di sconto su \
qualsiasi lavaggio. Ti aspettiamo! Rispondi STOP per non ricevere piu' \
promozioni."
- promo_dopo_pioggia [MARKETING]: "Ciao {{1}}! Che pioggia ieri... la tua \
auto ne avra' risentito! Solo per oggi e domani, lavaggio completo a {{2}} \
euro invece di {{3}}. Passa quando vuoi, senza prenotazione. Rispondi STOP \
per non ricevere piu' promozioni."
- secondo_lavaggio_nuovi [MARKETING]: "Ciao {{1}}, grazie per averci \
scelto! Per il tuo secondo lavaggio ti regaliamo l'igienizzazione interni \
(valore {{2}} euro). Valida fino al {{3}}. A presto! Rispondi STOP per non \
ricevere piu' promozioni."
- promemoria_tessera [UTILITY]: "Ciao {{1}}! Sulla tua tessera fedelta' hai \
{{2}} timbri: te ne mancano solo {{3}} per il lavaggio omaggio!"

# REGOLE FINALI

- Rispondi SEMPRE in italiano semplice: chi legge gestisce un autolavaggio, \
non un reparto marketing.
- Privacy: proponi invii solo verso chi e' contattabile (gli opt-out sono \
gia' esclusi dal sistema); mai pratiche contrarie al consenso.
- Quantita': max 6 best practice e max 4 voci ciascuno per \
proposte_segmenti, proposte_campagne, proposte_template e promozioni. \
Meglio poche proposte forti che tante deboli.
- Ogni motivazione cita i numeri del contesto che la giustificano.
- Nessun messaggio parte in automatico: le tue proposte sono bozze che \
l'operatore rivede e autorizza."""


def genera_analisi(user=None):
    """Chiama Claude, valida le proposte e salva una AnalisiAI.

    Solleva RuntimeError con messaggio parlante se la chiave manca o
    la chiamata fallisce (la vista lo mostra come messages.error).
    """
    import anthropic

    from apps.marketing.models import AnalisiAI

    if not ai_disponibile():
        raise RuntimeError(
            'Chiave API mancante: imposta la variabile ANTHROPIC_API_KEY '
            'su Railway (la trovi su console.anthropic.com).')

    contesto = contesto_per_ai()

    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=180.0,   # analisi lunghe: meglio aspettare che troncare
        max_retries=1,
    )
    try:
        # max_tokens abbondante: su Opus 5 il "ragionamento" adattivo
        # (attivo di default) consuma token da questo stesso budget
        # PRIMA del JSON finale — con un tetto basso la risposta arriva
        # troncata a meta' (JSONDecodeError gia' visto in produzione).
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=16000,
            system=_SYSTEM_PROMPT,
            messages=[{
                'role': 'user',
                'content': (
                    'Ecco i dati aggregati del CRM (JSON). Analizzali e '
                    'produci analisi, best practice e proposte operative.\n\n'
                    + json.dumps(contesto, ensure_ascii=False, default=str)
                ),
            }],
            output_config={
                'format': {'type': 'json_schema', 'schema': _SCHEMA_ANALISI},
            },
        )
    except anthropic.AuthenticationError:
        raise RuntimeError('Chiave API non valida: controlla ANTHROPIC_API_KEY.')
    except anthropic.RateLimitError:
        raise RuntimeError('Limite di richieste API raggiunto: riprova tra '
                           'qualche minuto.')
    except anthropic.APIError as e:
        logger.error('analisi AI fallita: %s', e)
        raise RuntimeError(f'Errore dalla Claude API: {e}')

    if response.stop_reason == 'max_tokens':
        # Il JSON e' certamente incompleto: inutile provare a leggerlo.
        logger.error('analisi AI troncata: max_tokens raggiunto '
                     '(in=%s out=%s)', response.usage.input_tokens,
                     response.usage.output_tokens)
        raise RuntimeError(
            'L\'analisi e\' risultata troppo lunga ed e\' stata troncata: '
            'riprova (di solito al secondo tentativo riesce).')

    testo = next(b.text for b in response.content if b.type == 'text')
    try:
        dati = json.loads(testo)
    except json.JSONDecodeError as e:
        logger.error('analisi AI: JSON non valido (%s). Inizio risposta: %s',
                     e, testo[:300])
        raise RuntimeError('La risposta dell\'AI non era leggibile: riprova.')

    # Etichetta lato server lo stato del template di ogni campagna
    # proposta (mai fidarsi solo del prompt): 'approvato' se e' nella
    # lista Meta, 'proposto' se e' una bozza di questa analisi (da
    # creare e approvare prima dell'invio), 'da_verificare' altrimenti.
    # Nessuna proposta viene scartata: la UI mostra il badge giusto.
    approvati = {t['nome'] for t in contesto['template_meta_approvati']}
    proposti = {t.get('nome') for t in dati.get('proposte_template', [])}
    campagne_ok = []
    for p in dati.get('proposte_campagne', []):
        nome_tpl = p.get('template_meta', '')
        if nome_tpl in approvati:
            stato = 'approvato'
        elif nome_tpl in proposti:
            stato = 'proposto'
        else:
            stato = 'da_verificare'
        campagne_ok.append({**p, 'stato_template': stato})

    return AnalisiAI.objects.create(
        creata_da=user if (user and user.is_authenticated) else None,
        modello=settings.ANTHROPIC_MODEL,
        contesto=contesto,
        analisi=dati.get('analisi', ''),
        best_practice=dati.get('best_practice', []),
        proposte_segmenti=dati.get('proposte_segmenti', []),
        proposte_campagne=campagne_ok,
        proposte_template=dati.get('proposte_template', []),
        promozioni=dati.get('promozioni', []),
        token_input=response.usage.input_tokens,
        token_output=response.usage.output_tokens,
    )
