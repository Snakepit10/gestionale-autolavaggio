"""Consulente AI marketing: analisi dati e proposte via Claude API.

Flusso: l'operatore preme "Genera analisi" -> si raccoglie un contesto
di SOLI aggregati anonimi (mai nomi/telefoni dei clienti) -> Claude
risponde in JSON strutturato (schema imposto dall'API) -> l'analisi
viene salvata in AnalisiAI e mostrata nella pagina AI.

Le proposte diventano operative SOLO con un click dell'operatore:
- proposta segmento  -> crea un SegmentoPersonalizzato
- proposta campagna  -> apre il composer precompilato (poi il solito
  flusso preview -> conferma: e' li' l'autorizzazione all'invio)
Vincolo concordato: le campagne proposte possono usare SOLO template
Meta gia' APPROVATI (la lista viene passata all'AI nel contesto e la
proposta viene comunque riverificata qui prima di mostrarla).

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


def _nomi_template_utilizzabili(templates: list[dict]) -> list[str]:
    """Nomi template proponibili: quelli approvati su Meta, oppure (se
    il listing non e' disponibile) quelli gia' usati con successo in
    campagne passate + il template del richiamo."""
    from apps.marketing.models import Campagna, ImpostazioniMarketing

    if templates:
        return [t['nome'] for t in templates]
    nomi = set(
        Campagna.objects.exclude(template_meta='')
        .values_list('template_meta', flat=True)
    )
    tpl_richiamo = ImpostazioniMarketing.get_solo().richiamo_template_meta
    if tpl_richiamo:
        nomi.add(tpl_richiamo)
    return sorted(nomi)


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

    cfg = ImpostazioniMarketing.get_solo()
    stats = statistiche_clienti()
    ris = segmenta_clienti(stats=stats)
    templates = template_approvati()

    return {
        'attivita': {
            'nome': 'Autolavaggio MasterWash',
            'luogo': 'Licata (AG), Via Palma 302',
            'canale_marketing': 'campagne WhatsApp con template Meta',
        },
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
                        'description': 'ESCLUSIVAMENTE un nome dalla lista '
                                       'template_meta_approvati del contesto.',
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
                 'proposte_campagne', 'promozioni'],
    'additionalProperties': False,
}

_SYSTEM_PROMPT = """Sei il consulente marketing di un autolavaggio italiano \
(Autolavaggio MasterWash, Licata). Analizzi i dati aggregati del loro CRM e \
proponi azioni concrete per aumentare i lavaggi e il fatturato.

Regole vincolanti:
- Rispondi SEMPRE in italiano, con tono pratico da consulente, senza gergo.
- Le campagne proposte devono usare ESCLUSIVAMENTE i nomi template presenti \
in template_meta_approvati nel contesto. Se la lista e' vuota, proponi \
campagne solo con i template gia' usati (visibili in ultime_campagne) e \
segnala nell'analisi che conviene far approvare template dedicati su Meta.
- Nei template_params usa i placeholder supportati ({nome}, \
{giorni_ultimo_lavaggio}, {totale_lavaggi}) o testo fisso, nell'ordine dei \
segnaposto {{1}}, {{2}}... del template scelto.
- I segmenti delle campagne devono essere chiavi esistenti nel contesto \
(segmenti_automatici o segmenti_personalizzati); se serve un segmento nuovo, \
proponilo in proposte_segmenti e NON usarlo nelle campagne di questa analisi.
- Proposte di segmento: compila solo i criteri utili, lascia null gli altri. \
Evita di duplicare segmenti gia' esistenti.
- Le promozioni devono essere realistiche per un autolavaggio (sconti %, \
bundle interni+esterni, gettoni omaggio del sistema monete, tessere lavaggi) \
e coerenti con i margini: mai regalare piu' del necessario.
- Rispetta i vincoli anti-spam del contesto (tetto giornaliero, fascia \
oraria, finestra no-ricontatto): non proporre volumi impossibili.
- Quantita': al massimo 6 best practice e al massimo 4 voci ciascuno per \
proposte_segmenti, proposte_campagne e promozioni. Meglio poche proposte \
forti che tante deboli.
- Basa TUTTO sui numeri del contesto: cita i dati che giustificano ogni \
proposta nella motivazione. Non inventare dati.
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
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=8000,
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

    testo = next(b.text for b in response.content if b.type == 'text')
    dati = json.loads(testo)

    # Riverifica lato server del vincolo template: mai fidarsi solo del
    # prompt. Le proposte con template non approvato vengono scartate.
    ammessi = set(_nomi_template_utilizzabili(
        contesto['template_meta_approvati']))
    campagne_ok, scartate = [], 0
    for p in dati.get('proposte_campagne', []):
        if ammessi and p.get('template_meta') not in ammessi:
            scartate += 1
            continue
        campagne_ok.append(p)
    if scartate:
        logger.warning('analisi AI: %d proposte campagna scartate per '
                       'template non approvato', scartate)

    return AnalisiAI.objects.create(
        creata_da=user if (user and user.is_authenticated) else None,
        modello=settings.ANTHROPIC_MODEL,
        contesto=contesto,
        analisi=dati.get('analisi', ''),
        best_practice=dati.get('best_practice', []),
        proposte_segmenti=dati.get('proposte_segmenti', []),
        proposte_campagne=campagne_ok,
        promozioni=dati.get('promozioni', []),
        token_input=response.usage.input_tokens,
        token_output=response.usage.output_tokens,
    )
