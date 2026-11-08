"""Budget e scostamenti del servito (Analitica > Budget).

Per ogni mese l'effettivo viene dalla Produttivita' (kpi_mese):
L = lavaggi, R = ricavi / L, V = consumi / L, ore pagate, c = personale /
ore, F = altri costi fissi = costo pieno - consumi - personale. Il budget
ha le stesse grandezze; un campo vuoto vale quanto lo stesso mese
dell'anno prima.

Risultato = L x R - L x V - ore x c - F. Gli scostamenti, come effetto
sul risultato (positivo = meglio del budget):
- volume sui ricavi = (L_eff - L_bdg) x R_bdg
- prezzo e mix = (R_eff - R_bdg) x L_eff
- consumi, volume = -(L_eff - L_bdg) x V_bdg
- consumi, spesa = -(V_eff - V_bdg) x L_eff
- ore attese = ore_bdg / L_bdg x L_eff
- ore per il volume = -(ore attese - ore_bdg) x c_bdg
- efficienza = -(ore_eff - ore attese) x c_bdg
- costo orario = -(c_eff - c_bdg) x ore_eff
- altri costi fissi = -(F_eff - F_bdg)
La somma e' risultato effettivo - risultato di budget."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from .conto_economico import NOMI_MESI, ZERO

GRANDEZZE = ('lavaggi', 'ricavo_lavaggio', 'consumi_lavaggio', 'ore_pagate', 'costo_orario', 'altri_fissi')
RIGHE_SCOSTAMENTI = [
    ('volume', 'Volume dei lavaggi sui ricavi'),
    ('prezzo', 'Prezzo e mix'),
    ('consumi_volume', 'Consumi per il volume'),
    ('consumi_spesa', 'Consumi di ogni lavaggio'),
    ('ore_volume', 'Ore per il volume'),
    ('efficienza', 'Efficienza (ore in più o in meno)'),
    ('costo_orario', 'Costo orario del personale'),
    ('fissi', 'Altri costi fissi'),
]


def _div(a, b):
    return a / b if b else None


def grandezze(k):
    """Dalle somme di kpi_mese alle grandezze del budget."""
    if not k or not k['lavaggi']:
        return None
    lavaggi = Decimal(k['lavaggi'])
    return {
        'lavaggi': lavaggi,
        'ricavo_lavaggio': k['ricavi'] / lavaggi,
        'consumi_lavaggio': k['variabili'] / lavaggi,
        'ore_pagate': k['ore'],
        'costo_orario': _div(k['personale'], k['ore']) or ZERO,
        'altri_fissi': k['pieno'] - k['variabili'] - k['personale'],
    }


def risultato(g):
    return (g['lavaggi'] * (g['ricavo_lavaggio'] - g['consumi_lavaggio']) - g['ore_pagate'] * g['costo_orario']
            - g['altri_fissi'])


def budget_del_mese(mese, base, salvato=None):
    """Grandezze di budget: quelle inserite, le altre dall'anno prima.
    None se ne manca qualcuna."""
    from .produttivita import kpi_mese
    prima = grandezze(kpi_mese(mese.replace(year=mese.year - 1), base)) or {}
    g = {}
    for campo in GRANDEZZE:
        valore = getattr(salvato, campo, None) if salvato else None
        g[campo] = Decimal(valore) if valore is not None else prima.get(campo)
    return g if all(v is not None for v in g.values()) else None


def scostamenti(e, b):
    """Effetto sul risultato di ogni scostamento (effettivo e contro budget b)."""
    attese = b['ore_pagate'] / b['lavaggi'] * e['lavaggi'] if b['lavaggi'] else b['ore_pagate']
    return {
        'volume': (e['lavaggi'] - b['lavaggi']) * b['ricavo_lavaggio'],
        'prezzo': (e['ricavo_lavaggio'] - b['ricavo_lavaggio']) * e['lavaggi'],
        'consumi_volume': -(e['lavaggi'] - b['lavaggi']) * b['consumi_lavaggio'],
        'consumi_spesa': -(e['consumi_lavaggio'] - b['consumi_lavaggio']) * e['lavaggi'],
        'ore_volume': -(attese - b['ore_pagate']) * b['costo_orario'],
        'efficienza': -(e['ore_pagate'] - attese) * b['costo_orario'],
        'costo_orario': -(e['costo_orario'] - b['costo_orario']) * e['ore_pagate'],
        'fissi': -(e['altri_fissi'] - b['altri_fissi']),
        'ore_attese': attese,
    }


def budget_e_scostamenti(mesi, anno, base):
    from .models import BudgetServito
    from .produttivita import kpi_mese
    salvati = {b.mese: b for b in BudgetServito.objects.filter(mese__year=anno)}
    oggi = timezone.localdate()
    # tabella da compilare: 12 mesi con il valore dell'anno prima come segnaposto
    modulo = []
    for n in range(1, 13):
        mese = date(anno, n, 1)
        prima = grandezze(kpi_mese(mese.replace(year=anno - 1), base)) or {}
        s = salvati.get(mese)
        modulo.append({'mese': mese, 'nome': NOMI_MESI[n - 1],
                       'campi': [{'campo': c, 'valore': getattr(s, c, None) if s else None, 'prima': prima.get(c)}
                                 for c in GRANDEZZE]})
    # scostamenti dei mesi del periodo con effettivo e budget
    totali = {chiave: ZERO for chiave, _ in RIGHE_SCOSTAMENTI}
    ris_eff = ris_bdg = ZERO
    eff_tot = {c: ZERO for c in ('lavaggi', 'ricavi', 'consumi', 'ore', 'personale', 'fissi')}
    bdg_tot = dict(eff_tot)
    contati, saltati = [], []
    for mese in mesi:
        if mese > oggi:
            continue
        e = grandezze(kpi_mese(mese, base))
        b = budget_del_mese(mese, base, salvati.get(mese))
        if not e or not b:
            saltati.append(NOMI_MESI[mese.month - 1])
            continue
        contati.append(NOMI_MESI[mese.month - 1])
        s = scostamenti(e, b)
        for chiave in totali:
            totali[chiave] += s[chiave]
        ris_eff += risultato(e)
        ris_bdg += risultato(b)
        for tot, g in ((eff_tot, e), (bdg_tot, b)):
            tot['lavaggi'] += g['lavaggi']
            tot['ricavi'] += g['lavaggi'] * g['ricavo_lavaggio']
            tot['consumi'] += g['lavaggi'] * g['consumi_lavaggio']
            tot['ore'] += g['ore_pagate']
            tot['personale'] += g['ore_pagate'] * g['costo_orario']
            tot['fissi'] += g['altri_fissi']
    confronto = None
    if contati:
        def riga(nome, chiave_e, formato, funzione):
            ve, vb = funzione(eff_tot), funzione(bdg_tot)
            return {'nome': nome, 'eff': ve, 'bdg': vb, 'diff': ve - vb if ve is not None and vb is not None else None,
                    'formato': formato}
        confronto = [
            riga('Lavaggi', 'lavaggi', 'intero', lambda t: t['lavaggi']),
            riga('Ricavo per lavaggio', '', 'euro2', lambda t: _div(t['ricavi'], t['lavaggi'])),
            riga('Consumi per lavaggio', '', 'euro2', lambda t: _div(t['consumi'], t['lavaggi'])),
            riga('Ore pagate', '', 'intero', lambda t: t['ore']),
            riga('Ore per lavaggio', '', 'due', lambda t: _div(t['ore'], t['lavaggi'])),
            riga('Costo orario del personale', '', 'euro2', lambda t: _div(t['personale'], t['ore'])),
            riga('Altri costi fissi', '', 'euro', lambda t: t['fissi']),
            riga('Ricavi', '', 'euro', lambda t: t['ricavi']),
        ]
    return {
        'modulo': modulo, 'campi': BudgetServito.CAMPI,
        'scostamenti': [{'chiave': k, 'nome': n, 'valore': totali[k]} for k, n in RIGHE_SCOSTAMENTI] if contati else None,
        'risultato_eff': ris_eff, 'risultato_bdg': ris_bdg, 'confronto': confronto,
        'contati': contati, 'saltati': saltati,
    }
