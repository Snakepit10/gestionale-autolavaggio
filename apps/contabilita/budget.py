"""Obiettivi, simulatore e scostamenti del servito (Analitica > Obiettivi).

Le leve di un mese (effettivo da kpi_mese, obiettivo da BudgetServito):
G giorni aperti, l auto al giorno, p prezzo del servizio principale
(ricavo per lavaggio meno gli extra), x % di lavaggi con extra, e valore
di un extra, V consumi per lavaggio, n persone al giorno, h ore per
persona al giorno, c costo orario, S altri costi fissi (costo pieno -
consumi - personale, quota dei costi comuni compresa).

Calcoli: L = G x l; E = x x e (extra per lavaggio); R = p + E;
ore = G x n x h; personale = ore x c;
risultato = L x (R - V) - personale - S;
costo pieno per lavaggio = V + (personale + S) / L;
auto al giorno a pareggio = (personale + S) / (G x (R - V));
per il margine obiettivo m = (personale + S) / (G x (R - V - m x R)).

Ponte da un riferimento A (obiettivo o mese precedente) all'effettivo B,
come effetto sul risultato (positivo = meglio):
- giorni di apertura = (G_B - G_A) x l_A x (R_A - V_A)
- affluenza = G_B x (l_B - l_A) x (R_A - V_A)
- prezzo dei servizi principali = (p_B - p_A) x L_B
- extra = (E_B - E_A) x L_B
- consumi = -(V_B - V_A) x L_B
- ore attese = ore_A / L_A x L_B
- ore per il volume = -(ore attese - ore_A) x c_A
- efficienza = -(ore_B - ore attese) x c_A
- paghe = -(c_B - c_A) x ore_B
- altri costi fissi = -(S_B - S_A)
La somma e' risultato_B - risultato_A. Il volume vale il margine per
lavaggio (R - V), non tutto lo scontrino."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from .conto_economico import IVA, NOMI_MESI, ZERO, fine_mese
from .models import BudgetServito

LEVE = [campo for campo, _ in BudgetServito.CAMPI]
COSTI = ('consumi_lavaggio', 'persone_giorno', 'ore_giornata', 'costo_orario', 'altri_fissi')
# leve che il simulatore salva come obiettivo: giorni e altri fissi restano quelli del mese
LEVE_PER_GIORNO = [c for c in LEVE if c not in ('giorni_aperti', 'altri_fissi')]
DECIMALI = {'giorni_aperti': 0, 'auto_giorno': 2, 'prezzo_principale': 2, 'pct_extra': 1, 'valore_extra': 2,
            'consumi_lavaggio': 2, 'persone_giorno': 2, 'ore_giornata': 2, 'costo_orario': 2, 'altri_fissi': 0}
# nel simulatore qualche decimale in piu': arrotondando persone e ore il risultato di partenza si sposta di centinaia di euro
DECIMALI_SIMULATORE = {**DECIMALI, 'auto_giorno': 3, 'prezzo_principale': 3, 'pct_extra': 2, 'valore_extra': 3,
                       'consumi_lavaggio': 3, 'persone_giorno': 4, 'ore_giornata': 4, 'costo_orario': 3}

RIGHE_PONTE = [
    ('giorni', 'Giorni di apertura'),
    ('auto_giorno', 'Affluenza (auto al giorno)'),
    ('prezzo', 'Prezzo dei servizi principali'),
    ('extra', 'Extra (upselling)'),
    ('consumi', 'Consumi di ogni lavaggio'),
    ('ore_volume', 'Ore per il volume'),
    ('efficienza', 'Efficienza (ore in più o in meno)'),
    ('costo_orario', 'Paghe (costo orario)'),
    ('fissi', 'Altri costi fissi'),
]

# (chiave, nome, formato, meglio se sale)
INDICATORI = [
    ('auto_giorno', 'Auto al giorno', 'uno', True),
    ('prezzo', 'Prezzo del servizio principale', 'euro2', True),
    ('extra', 'Ricavo extra per lavaggio', 'euro2', True),
    ('pct_extra', 'Lavaggi con extra', 'pct', True),
    ('consumi', 'Consumi per lavaggio', 'euro2', False),
    ('persone', 'Persone medie al giorno', 'due', None),
    ('lavaggi_persona', 'Lavaggi per persona al giorno', 'uno', True),
    ('ore_lavaggio', 'Ore per lavaggio', 'due', False),
    ('costo_orario', 'Costo orario', 'euro2', False),
    ('costo_pieno', 'Costo pieno per lavaggio', 'euro2', False),
    ('risultato', 'Risultato pieno servito', 'euro', True),
]


def _div(a, b):
    return a / b if b else None


# ---------------------------------------------------------------- leve e calcoli

def grandezze(k):
    """Leve effettive dalle somme di kpi_mese. None senza lavaggi; senza
    presenze (costi del personale non registrati, come nel 2025) mancano
    le leve dei costi."""
    if not k or not k['lavaggi'] or not k['giorni_aperti']:
        return None
    lavaggi, giorni = Decimal(k['lavaggi']), Decimal(k['giorni_aperti'])
    extra, con_extra = k.get('extra', ZERO), k.get('con_extra', 0)
    g = {
        'giorni_aperti': giorni,
        'auto_giorno': lavaggi / giorni,
        'prezzo_principale': (k['ricavi'] - extra) / lavaggi,
        'pct_extra': Decimal(con_extra) * 100 / lavaggi,
        'valore_extra': extra / con_extra if con_extra else ZERO,
    }
    if k['ore'] and k['giorni_persona']:
        g.update({
            'consumi_lavaggio': k['variabili'] / lavaggi,
            'persone_giorno': k['giorni_persona'] / giorni,
            'ore_giornata': k['ore'] / k['giorni_persona'],
            'costo_orario': k['personale'] / k['ore'],
            'altri_fissi': k['pieno'] - k['variabili'] - k['personale'],
        })
    return g


def completo(g):
    return bool(g) and all(g.get(c) is not None for c in LEVE)


def calcola(g, margine_obiettivo=None):
    """Lavaggi, ricavi, costi, risultato e costo pieno dalle leve."""
    giorni, auto = g['giorni_aperti'], g['auto_giorno']
    lavaggi = giorni * auto
    extra = g['pct_extra'] / 100 * g['valore_extra']
    ricavo = g['prezzo_principale'] + extra
    consumi = g['consumi_lavaggio']
    ore = giorni * g['persone_giorno'] * g['ore_giornata']
    personale = ore * g['costo_orario']
    fissi = personale + g['altri_fissi']
    ricavi = lavaggi * ricavo
    ris = lavaggi * (ricavo - consumi) - fissi
    r = {
        'lavaggi': lavaggi, 'extra': extra, 'ricavo': ricavo, 'ore': ore, 'personale': personale,
        'ricavi': ricavi, 'consumi': lavaggi * consumi, 'risultato': ris,
        'costo_pieno': consumi + fissi / lavaggi if lavaggi else None,
        'margine': _div(ris * 100, ricavi),
        'lavaggi_persona': _div(auto, g['persone_giorno']),
        'ore_lavaggio': _div(ore, lavaggi),
        'pareggio_auto': fissi / (giorni * (ricavo - consumi)) if giorni and ricavo > consumi else None,
    }
    if margine_obiettivo is not None:
        resta = ricavo - consumi - Decimal(margine_obiettivo) / 100 * ricavo
        r['obiettivo_auto'] = fissi / (giorni * resta) if giorni and resta > 0 else None
    return r


def ponte(a, b):
    """Effetto di ogni leva sul risultato, dal riferimento a all'effettivo b."""
    ca, cb = calcola(a), calcola(b)
    margine_a = ca['ricavo'] - a['consumi_lavaggio']
    attese = ca['ore'] / ca['lavaggi'] * cb['lavaggi'] if ca['lavaggi'] else ca['ore']
    costo_a = a['costo_orario']
    return {
        'giorni': (b['giorni_aperti'] - a['giorni_aperti']) * a['auto_giorno'] * margine_a,
        'auto_giorno': b['giorni_aperti'] * (b['auto_giorno'] - a['auto_giorno']) * margine_a,
        'prezzo': (b['prezzo_principale'] - a['prezzo_principale']) * cb['lavaggi'],
        'extra': (cb['extra'] - ca['extra']) * cb['lavaggi'],
        'consumi': -(b['consumi_lavaggio'] - a['consumi_lavaggio']) * cb['lavaggi'],
        'ore_volume': -(attese - ca['ore']) * costo_a,
        'efficienza': -(cb['ore'] - attese) * costo_a,
        'costo_orario': -(b['costo_orario'] - costo_a) * cb['ore'],
        'fissi': -(b['altri_fissi'] - a['altri_fissi']),
    }


def arrotonda(g, decimali=DECIMALI):
    return {c: g[c].quantize(Decimal(1).scaleb(-decimali[c])) for c in LEVE}


def valore_delle_leve(g, margine_obiettivo):
    """Quanto cambia il risultato muovendo una leva alla volta."""
    base = calcola(g)
    prove = [
        ('+1 auto al giorno, stesso personale', {'auto_giorno': g['auto_giorno'] + 1}),
        ('+€1 sui prezzi di listino (IVA inclusa)', {'prezzo_principale': g['prezzo_principale'] + 1 / IVA}),
        ('+10 punti di lavaggi con extra', {'pct_extra': min(g['pct_extra'] + 10, Decimal(100))}),
        ('+€1 sul valore di un extra (IVA inclusa)', {'valore_extra': g['valore_extra'] + 1 / IVA}),
        ('−1 ora pagata al giorno, stesse auto', {'ore_giornata': max(g['ore_giornata'] - 1 / g['persone_giorno'], ZERO)}),
        ('−10% delle ore di personale', {'persone_giorno': g['persone_giorno'] * Decimal('0.9')}),
        ('−€0,50 di costo orario', {'costo_orario': max(g['costo_orario'] - Decimal('0.5'), ZERO)}),
        ('−10% dei consumi', {'consumi_lavaggio': g['consumi_lavaggio'] * Decimal('0.9')}),
        ('−10% di struttura e costi comuni', {'altri_fissi': g['altri_fissi'] * Decimal('0.9')}),
    ]
    righe = []
    for nome, cambio in prove:
        r = calcola({**g, **cambio})
        diff = (r['costo_pieno'] - base['costo_pieno']
                if r['costo_pieno'] is not None and base['costo_pieno'] is not None else None)
        righe.append({'nome': nome, 'effetto': r['risultato'] - base['risultato'], 'costo_pieno': r['costo_pieno'],
                      'diff_costo': diff if diff is not None and abs(diff) >= Decimal('0.005') else None})
    # quanti lavaggi si possono perdere con +1 euro al listino prima di guadagnare meno
    nuovo = base['ricavo'] + 1 / IVA
    perdita = (nuovo - base['ricavo']) / (nuovo - g['consumi_lavaggio']) if nuovo > g['consumi_lavaggio'] else None
    return {'righe': righe, 'base': base, 'perdita_prezzo': perdita * 100 if perdita is not None else None,
            'perdita_auto': perdita * g['auto_giorno'] if perdita is not None else None,
            'calcolo': calcola(g, margine_obiettivo)}


# ---------------------------------------------------------------- obiettivo del mese

def riserva_costi(anno, base):
    """Leve dei costi di ripiego, quando lo stesso mese dell'anno prima non
    ha presenze (come il 2025): la media dei mesi chiusi dell'anno con
    presenze, o dell'anno prima se l'anno non ne ha. Le persone si
    ricavano dalle auto al giorno con i lavaggi per persona medi."""
    from .produttivita import kpi_mese, somma_kpi
    inizio = timezone.localdate().replace(day=1)
    for a in (anno, anno - 1):
        validi = []
        for n in range(1, 13):
            mese = date(a, n, 1)
            if fine_mese(mese) >= inizio:
                break
            k = kpi_mese(mese, base)
            if k and k['lavaggi'] and k['ore'] and k['giorni_persona'] and k['giorni_aperti']:
                validi.append(k)
        if validi:
            t = somma_kpi(validi)
            g = grandezze(t)
            return {'consumi_lavaggio': g['consumi_lavaggio'], 'ore_giornata': g['ore_giornata'],
                    'costo_orario': g['costo_orario'], 'altri_fissi': g['altri_fissi'] / len(validi),
                    'lavaggi_persona': Decimal(t['lavaggi']) / t['giorni_persona'], 'anno': a}
    return None


def valori_del_mese(mese, base, salvato=None, riserva=None):
    """{campo: (valore, origine)}. Origine: 'obiettivo' se inserito, 'anno
    prima' se dallo stesso mese dell'anno prima, 'media' se dalla riserva
    dei costi, None se manca."""
    from .produttivita import kpi_mese
    prima = grandezze(kpi_mese(mese.replace(year=mese.year - 1), base)) or {}
    valori = {}
    for campo in LEVE:
        inserito = getattr(salvato, campo, None) if salvato else None
        if inserito is not None:
            valori[campo] = (Decimal(inserito), 'obiettivo')
        elif prima.get(campo) is not None:
            valori[campo] = (prima[campo], 'anno prima')
        else:
            valori[campo] = (None, None)
    if riserva:
        for campo in COSTI:
            if valori[campo][0] is not None:
                continue
            if campo == 'persone_giorno':
                auto = valori['auto_giorno'][0]
                v = auto / riserva['lavaggi_persona'] if auto is not None and riserva['lavaggi_persona'] else None
            else:
                v = riserva[campo]
            valori[campo] = (v, 'media' if v is not None else None)
    return valori


def budget_del_mese(mese, base, salvato=None, riserva=None):
    """Leve di obiettivo complete del mese, o None se ne manca qualcuna."""
    g = {c: v for c, (v, _) in valori_del_mese(mese, base, salvato, riserva).items()}
    return g if completo(g) else None


# ---------------------------------------------------------------- totali e indicatori

def _totali(g):
    r = calcola(g)
    return {'giorni': g['giorni_aperti'], 'lavaggi': r['lavaggi'], 'ricavi': r['ricavi'],
            'extra': r['extra'] * r['lavaggi'], 'con_extra': g['pct_extra'] / 100 * r['lavaggi'],
            'consumi': r['consumi'], 'giorni_persona': g['giorni_aperti'] * g['persone_giorno'], 'ore': r['ore'],
            'personale': r['personale'], 'fissi': g['altri_fissi'], 'risultato': r['risultato']}


def _somma(elenco):
    elenco = [t for t in elenco if t]
    if not elenco:
        return None
    return {k: sum((t[k] for t in elenco), ZERO) for k in elenco[0]}


def indicatori(t):
    if not t:
        return None
    lavaggi = t['lavaggi']
    return {
        'giorni': t['giorni'], 'lavaggi': lavaggi, 'ricavi': t['ricavi'], 'fissi': t['fissi'],
        'auto_giorno': _div(lavaggi, t['giorni']),
        'prezzo': _div(t['ricavi'] - t['extra'], lavaggi),
        'extra': _div(t['extra'], lavaggi),
        'pct_extra': _div(t['con_extra'] * 100, lavaggi),
        'consumi': _div(t['consumi'], lavaggi),
        'persone': _div(t['giorni_persona'], t['giorni']),
        'lavaggi_persona': _div(lavaggi, t['giorni_persona']),
        'ore_lavaggio': _div(t['ore'], lavaggi),
        'costo_orario': _div(t['personale'], t['ore']),
        'costo_pieno': _div(t['consumi'] + t['personale'] + t['fissi'], lavaggi),
        'risultato': t['risultato'],
    }


def _buono(v, obiettivo, meglio_su):
    """True se v e' meglio dell'obiettivo, False se peggio, None se pari
    (entro l'1%) o non confrontabile."""
    if v is None or obiettivo is None or meglio_su is None:
        return None
    soglia = abs(obiettivo) / 100
    if abs(v - obiettivo) <= soglia:
        return None
    return (v > obiettivo) == meglio_su


# ---------------------------------------------------------------- pagina

def budget_e_scostamenti(mesi, anno, base):
    from .models import ParametriAnalitica
    from .produttivita import kpi_mese, somma_kpi
    salvati = {b.mese: b for b in BudgetServito.objects.filter(mese__year=anno)}
    oggi = timezone.localdate()
    inizio = oggi.replace(day=1)
    margine_obiettivo = ParametriAnalitica.attuali().margine_obiettivo
    riserva = riserva_costi(anno, base)

    def effettivo(mese):
        g = grandezze(kpi_mese(mese, base))
        return g if completo(g) else None

    modulo, colonne = [], []
    precedente = effettivo(date(anno - 1, 12, 1))
    for n in range(1, 13):
        mese = date(anno, n, 1)
        s = salvati.get(mese)
        valori = valori_del_mese(mese, base, s, riserva)
        modulo.append({'mese': mese, 'nome': NOMI_MESI[n - 1], 'campi': [
            {'campo': c, 'valore': getattr(s, c, None) if s else None,
             'segnaposto': v if origine != 'obiettivo' else None, 'origine': origine,
             'decimali': DECIMALI[c]}
            for c, (v, origine) in valori.items()]})
        b = {c: v for c, (v, _) in valori.items()}
        b = b if completo(b) else None
        e = effettivo(mese) if mese <= oggi else None
        chiuso = fine_mese(mese) < inizio
        colonne.append({
            'mese': mese, 'nome': NOMI_MESI[n - 1], 'chiuso': chiuso, 'in_corso': mese == inizio,
            'e': e, 'b': b, 'p': precedente,
            'ponte_b': ponte(b, e) if chiuso and e and b else None,
            'ponte_p': ponte(precedente, e) if chiuso and e and precedente else None,
        })
        precedente = e

    # periodo scelto: mesi chiusi con effettivo e obiettivo
    nel_periodo = [c for c in colonne if c['mese'] in mesi]
    contati = [c for c in nel_periodo if c['ponte_b']]
    aggregato = None
    if contati:
        aggregato = {
            'scostamenti': [{'chiave': k, 'nome': nome, 'valore': sum((c['ponte_b'][k] for c in contati), ZERO)}
                            for k, nome in RIGHE_PONTE],
            'risultato_bdg': sum((calcola(c['b'])['risultato'] for c in contati), ZERO),
            'risultato_eff': sum((calcola(c['e'])['risultato'] for c in contati), ZERO),
        }
        ie = indicatori(_somma([_totali(c['e']) for c in contati]))
        ib = indicatori(_somma([_totali(c['b']) for c in contati]))
        aggregato['confronto'] = [
            {'nome': nome, 'formato': formato, 'eff': ie[k], 'bdg': ib[k],
             'diff': ie[k] - ib[k] if ie[k] is not None and ib[k] is not None else None,
             'buono': _buono(ie[k], ib[k], meglio)}
            for k, nome, formato, meglio in [('lavaggi', 'Lavaggi', 'intero', True), ('giorni', 'Giorni aperti', 'intero', None)]
            + INDICATORI]

    # mese su mese: colonne dei mesi gia' iniziati (tutti, per gli anni passati)
    visibili = [c for c in colonne if c['mese'] <= oggi]

    def tabella_ponte(chiave_ponte, chiave_rif, inizio_nome, fine_nome, con_totale):
        righe = []
        rif = [calcola(c[chiave_rif])['risultato'] if c[chiave_ponte] else None for c in visibili]
        eff = [calcola(c['e'])['risultato'] if c[chiave_ponte] else None for c in visibili]

        def totale(valori):
            presenti = [v for v in valori if v is not None]
            return sum(presenti, ZERO) if con_totale and presenti else None
        righe.append({'nome': inizio_nome, 'tipo': 'risultato', 'celle': rif + [totale(rif)]})
        for k, nome in RIGHE_PONTE:
            celle = [c[chiave_ponte][k] if c[chiave_ponte] else None for c in visibili]
            righe.append({'nome': nome, 'tipo': 'effetto', 'celle': celle + [totale(celle)]})
        righe.append({'nome': fine_nome, 'tipo': 'uguale', 'celle': eff + [totale(eff)]})
        return righe if any(v is not None for v in eff) else None

    tabella_indicatori = []
    con_dati = [c for c in visibili if c['e']]
    tot_e = indicatori(_somma([_totali(c['e']) for c in con_dati]))
    tot_b = indicatori(_somma([_totali(c['b']) for c in con_dati])) if all(c['b'] for c in con_dati) else None
    for k, nome, formato, meglio in INDICATORI:
        celle = []
        for c in visibili:
            ie = indicatori(_totali(c['e'])) if c['e'] else None
            ib = indicatori(_totali(c['b'])) if c['b'] else None
            v, o = (ie or {}).get(k), (ib or {}).get(k)
            celle.append({'v': v, 'obj': o, 'buono': _buono(v, o, meglio)})
        v, o = (tot_e or {}).get(k), (tot_b or {}).get(k)
        celle.append({'v': v, 'obj': o, 'buono': _buono(v, o, meglio)})
        tabella_indicatori.append({'nome': nome, 'formato': formato, 'celle': celle})

    # simulatore: parte dalle leve effettive del periodo scelto
    effettivo_periodo = grandezze(somma_kpi([kpi_mese(m, base) for m in mesi]))
    simulatore = None
    if completo(effettivo_periodo):
        leve = arrotonda(effettivo_periodo, DECIMALI_SIMULATORE)
        simulatore = {
            'leve': [{'campo': c, 'nome': nome, 'valore': leve[c], 'decimali': DECIMALI[c],
                      'per_giorno': c in LEVE_PER_GIORNO} for c, nome in BudgetServito.CAMPI],
            'valore': valore_delle_leve(leve, margine_obiettivo),
            'json': {'campi': LEVE, 'base': {c: float(v) for c, v in leve.items()},
                     'margine': float(margine_obiettivo), 'iva': float(IVA)},
        }

    return {
        'modulo': modulo, 'campi': BudgetServito.CAMPI, 'riserva': riserva,
        'aggregato': aggregato,
        'contati': [c['nome'] for c in contati],
        'saltati': [c['nome'] for c in nel_periodo if c['chiuso'] and not c['ponte_b']],
        'colonne': [{'nome': c['nome'], 'in_corso': c['in_corso'], 'chiuso': c['chiuso']} for c in visibili],
        'ponte_obiettivo': tabella_ponte('ponte_b', 'b', 'Risultato obiettivo', 'Risultato effettivo', True),
        'ponte_precedente': tabella_ponte('ponte_p', 'p', 'Risultato del mese prima', 'Risultato del mese', False),
        'indicatori': tabella_indicatori if con_dati else None,
        'simulatore': simulatore,
        'margine_obiettivo': margine_obiettivo,
        'leve_per_giorno': LEVE_PER_GIORNO,
    }
