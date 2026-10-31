"""Calcolo del costo del personale, con le stesse regole del foglio
Excel "Registrazione mensile":

- ore totali = ore lavorate + giorni F x ore/giorno ferie + giorni M x
  ore/giorno malattia (L = libero, non pagato);
- netto = ore totali x tariffa; rateo 13a = netto / 12 (solo
  contrattualizzati); lordo = netto x coefficiente;
- contributi = busta paga x (coefficiente - 1);
- differenza fuori busta = netto + 13a - busta paga; totale da pagare =
  differenza + bonus; residuo = totale - pagato;
- costo aziendale = netto + 13a + contributi + bonus.

Tariffa e coefficiente vengono dai parametri con il "valido dal" piu'
recente non successivo al mese; il coefficiente si puo' sovrascrivere
nel mese (RigaMese.coefficiente_lordo)."""
import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .models import MesePersonale, ParametroRetributivo, Presenza

ZERO = Decimal('0')
ORE_MAX = Decimal('16')
GIORNI_SETTIMANA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
NOMI_MESI = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio',
             'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']


def primo_del_mese(giorno):
    return giorno.replace(day=1)


def giorni_del_mese(mese):
    n = calendar.monthrange(mese.year, mese.month)[1]
    return [mese + timedelta(days=i) for i in range(n)]


def mese_successivo(mese):
    return (mese + timedelta(days=32)).replace(day=1)


def mese_precedente(mese):
    return (mese - timedelta(days=1)).replace(day=1)


def testo_numero(valore):
    """Decimal -> testo per i campi (8,5 / 10 / 1,17); None -> ''."""
    if valore is None or valore == '':
        return ''
    if not isinstance(valore, Decimal):
        return str(valore)
    t = f'{valore:f}'
    if '.' in t:
        t = t.rstrip('0').rstrip('.')
    return t.replace('.', ',')


def leggi_cella(testo):
    """Valore di una cella del calendario -> (ore, assenza). Accetta ore
    da 0 a 16 (anche con la virgola), F, M, L o vuoto. ValueError se no."""
    t = str(testo if testo is not None else '').strip().upper().replace(',', '.')
    if t == '':
        return None, ''
    if t in ('F', 'M', 'L'):
        return None, t
    try:
        ore = Decimal(t)
    except InvalidOperation:
        raise ValueError(f'"{testo}" non valido: ore da 0 a 16 oppure F, M, L')
    if not ZERO <= ore <= ORE_MAX:
        raise ValueError(f'"{testo}" non valido: le ore vanno da 0 a 16')
    return ore.quantize(Decimal('0.01')), ''


def parametri_per(dipendente, mese, parametri=None):
    """Parametri in vigore nel mese (o None). `parametri` = lista gia'
    caricata dei parametri del dipendente, per non rifare la query."""
    if parametri is None:
        return (ParametroRetributivo.objects.filter(dipendente=dipendente, valido_dal__lte=mese)
                .order_by('-valido_dal').first())
    validi = [p for p in parametri if p.valido_dal <= mese]
    return max(validi, key=lambda p: p.valido_dal) if validi else None


def calcola_riga(riga, presenze, giorni, mese_obj, parametro):
    """Tutte le colonne di un dipendente del mese."""
    dip = riga.dipendente
    per_data = {p.data: p for p in presenze}
    celle = [{'giorno': g.day, 'dom': g.weekday() == 6,
              'v': testo_numero(per_data[g].valore) if g in per_data else ''} for g in giorni]
    ore_lavorate = sum((p.ore or ZERO for p in presenze if not p.assenza), ZERO)
    gg = {c: sum(1 for p in presenze if p.assenza == c) for c in 'FML'}
    ore_ferie = gg['F'] * mese_obj.ore_giorno_ferie
    ore_malattia = gg['M'] * mese_obj.ore_giorno_malattia
    ore_totali = ore_lavorate + ore_ferie + ore_malattia

    tariffa = parametro.tariffa_oraria if parametro else None
    coeff_parametri = parametro.coefficiente_lordo if parametro else None
    coeff = riga.coefficiente_lordo if riga.coefficiente_lordo is not None else coeff_parametri

    netto = ore_totali * (tariffa or ZERO)
    rateo = netto / 12 if dip.contrattualizzato else ZERO
    lordo = netto * (coeff or ZERO)
    busta = riga.busta_paga or ZERO
    bonus = riga.bonus or ZERO
    contributi = busta * ((coeff or Decimal('1')) - 1)
    differenza = netto + rateo - busta
    totale_pagare = differenza + bonus
    residuo = totale_pagare - (riga.pagato or ZERO)
    costo = netto + rateo + contributi + bonus
    return {
        'riga': riga, 'dipendente': dip, 'celle': celle,
        'ore_lavorate': ore_lavorate, 'gg_ferie': gg['F'], 'gg_malattia': gg['M'], 'gg_libero': gg['L'],
        'ore_ferie': ore_ferie, 'ore_malattia': ore_malattia, 'ore_totali': ore_totali,
        'parametro': parametro, 'tariffa': tariffa,
        'coeff': coeff, 'coeff_parametri': coeff_parametri,
        'coeff_sovrascritto': riga.coefficiente_lordo is not None and riga.coefficiente_lordo != coeff_parametri,
        'netto': netto, 'rateo': rateo, 'lordo': lordo,
        'busta_paga': riga.busta_paga, 'contributi': contributi, 'bonus': riga.bonus,
        'differenza': differenza, 'totale_pagare': totale_pagare,
        'pagato': riga.pagato, 'residuo': residuo, 'costo': costo,
        # testo dei campi da compilare
        'inp': {'coeff': testo_numero(riga.coefficiente_lordo), 'busta': testo_numero(riga.busta_paga),
                'bonus': testo_numero(riga.bonus), 'pagato': testo_numero(riga.pagato)},
    }


def _controlli(righe):
    def controllo(nome, mancano, messaggio):
        return {'nome': nome, 'ok': not mancano,
                'messaggio': 'OK' if not mancano else f'{messaggio}: {", ".join(mancano)}'}
    con_ore = [r for r in righe if r['ore_totali'] > 0]
    return [
        controllo('Parametri validi per il mese',
                  [r['dipendente'].nome for r in righe if r['parametro'] is None],
                  'Manca una riga di parametri con "valido dal" entro il mese'),
        controllo('Tariffa inserita',
                  [r['dipendente'].nome for r in con_ore if not r['tariffa']],
                  'Tariffa mancante'),
        controllo('Coeff. lordo inserito',
                  [r['dipendente'].nome for r in righe if r['dipendente'].contrattualizzato and not r['coeff']],
                  'Coefficiente lordo mancante'),
        controllo('Busta paga inserita',
                  [r['dipendente'].nome for r in con_ore
                   if r['dipendente'].contrattualizzato and r['busta_paga'] is None],
                  'Busta paga mancante'),
    ]


TOTALI = ['ore_lavorate', 'gg_ferie', 'gg_malattia', 'gg_libero', 'ore_ferie', 'ore_malattia',
          'ore_totali', 'netto', 'rateo', 'lordo', 'contributi', 'differenza', 'totale_pagare',
          'residuo', 'costo']


def calcola_mese(mese_obj):
    """Calendario, calcolo per dipendente, totali, controlli e riepilogo."""
    mese = mese_obj.mese
    giorni = giorni_del_mese(mese)
    righe_mese = list(mese_obj.righe.select_related('dipendente'))
    dip_ids = [r.dipendente_id for r in righe_mese]
    presenze = {}
    for p in Presenza.objects.filter(dipendente_id__in=dip_ids, data__gte=giorni[0], data__lte=giorni[-1]):
        presenze.setdefault(p.dipendente_id, []).append(p)
    parametri = {}
    for p in ParametroRetributivo.objects.filter(dipendente_id__in=dip_ids):
        parametri.setdefault(p.dipendente_id, []).append(p)

    righe = [calcola_riga(r, presenze.get(r.dipendente_id, []), giorni, mese_obj,
                          parametri_per(r.dipendente, mese, parametri.get(r.dipendente_id, [])))
             for r in righe_mese]
    totali = {k: sum((r[k] for r in righe), ZERO) for k in TOTALI}
    for k in ('busta_paga', 'bonus', 'pagato'):
        totali[k] = sum((r[k] or ZERO for r in righe), ZERO)
    ore_giorno = [{'giorno': g.day, 'dom': g.weekday() == 6,
                   'ore': sum((p.ore or ZERO for ps in presenze.values() for p in ps if p.data == g and not p.assenza), ZERO)}
                  for g in giorni]
    controlli = _controlli(righe)
    attivi = sum(1 for r in righe if r['ore_totali'] > 0)
    return {
        'mese': mese_obj,
        'giorni': [{'data': g, 'sett': GIORNI_SETTIMANA[g.weekday()], 'domenica': g.weekday() == 6}
                   for g in giorni],
        'righe': righe,
        'ore_giorno': ore_giorno,
        'totali': totali,
        'controlli': controlli,
        'da_verificare': sum(1 for c in controlli if not c['ok']),
        'riepilogo': {
            'ore': totali['ore_totali'],
            'netto': totali['netto'],
            'lordo': totali['lordo'],
            'costo': totali['costo'],
            'attivi': attivi,
            'costo_ora': totali['costo'] / totali['ore_totali'] if totali['ore_totali'] else ZERO,
        },
    }


def _variazione(attuale, precedente):
    if not precedente or not attuale:
        return None
    return (attuale - precedente) / precedente * 100


def andamento_anno(anno):
    """Andamento mese per mese, per trimestre e per dipendente. I mesi
    senza calcolo valgono 0 e non entrano nelle medie (come nel
    prospetto Excel)."""
    mesi_obj = {m.mese.month: m for m in MesePersonale.objects.filter(mese__year=anno)}
    mesi, dipendenti = [], {}
    precedente = None
    for n in range(1, 13):
        mese_obj = mesi_obj.get(n)
        calcolo = calcola_mese(mese_obj) if mese_obj else None
        r = calcolo['riepilogo'] if calcolo else None
        riga = {
            'numero': n, 'nome': NOMI_MESI[n - 1], 'data': date(anno, n, 1),
            'trimestre': (n - 1) // 3 + 1, 'presente': bool(calcolo),
            'ore': r['ore'] if r else ZERO, 'attivi': r['attivi'] if r else 0,
            'netto': r['netto'] if r else ZERO, 'lordo': r['lordo'] if r else ZERO,
            'costo': r['costo'] if r else ZERO, 'costo_ora': r['costo_ora'] if r else ZERO,
        }
        riga['costo_medio_dip'] = riga['costo'] / riga['attivi'] if riga['attivi'] else ZERO
        riga['variazione'] = _variazione(riga['costo'], precedente['costo'] if precedente else None)
        if riga['costo']:
            precedente = riga
        mesi.append(riga)
        for rd in (calcolo['righe'] if calcolo else []):
            d = dipendenti.setdefault(rd['dipendente'].pk, {
                'dipendente': rd['dipendente'], 'ore': [ZERO] * 12, 'costo': [ZERO] * 12})
            d['ore'][n - 1] = rd['ore_totali']
            d['costo'][n - 1] = rd['costo']

    con_dati = [m for m in mesi if m['costo'] or m['ore']]
    totale = {k: sum((m[k] for m in mesi), ZERO) for k in ('ore', 'netto', 'lordo', 'costo')}
    totale['costo_ora'] = totale['costo'] / totale['ore'] if totale['ore'] else ZERO
    totale['costo_medio_mese'] = totale['costo'] / len(con_dati) if con_dati else ZERO
    totale['mesi'] = len(con_dati)

    trimestri, prec_t = [], None
    for t in range(1, 5):
        dentro = [m for m in mesi if m['trimestre'] == t and (m['costo'] or m['ore'])]
        tr = {'numero': t, 'mesi': len(dentro)}
        for k in ('ore', 'netto', 'lordo', 'costo'):
            tr[k] = sum((m[k] for m in dentro), ZERO)
        tr['costo_ora'] = tr['costo'] / tr['ore'] if tr['ore'] else ZERO
        tr['variazione'] = _variazione(tr['costo'], prec_t['costo'] if prec_t else None)
        if tr['costo']:
            prec_t = tr
        trimestri.append(tr)

    per_dipendente = sorted(dipendenti.values(), key=lambda d: (d['dipendente'].ordine, d['dipendente'].nome))
    for d in per_dipendente:
        d['ore_totale'] = sum(d['ore'], ZERO)
        d['costo_totale'] = sum(d['costo'], ZERO)
        n = sum(1 for c in d['costo'] if c)
        d['costo_medio'] = d['costo_totale'] / n if n else ZERO
    return {'anno': anno, 'mesi': mesi, 'totale': totale, 'trimestri': trimestri,
            'per_dipendente': per_dipendente}
