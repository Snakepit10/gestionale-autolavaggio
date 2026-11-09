"""Calcoli del report dei tempi di lavorazione (fasi rilevate dagli operatori)."""
from datetime import date, timedelta
from statistics import median

from django.utils import timezone

from apps.turni.models import LavorazioneOperatore

PERIODI = [
    ('oggi', 'Oggi'),
    ('7g', 'Ultimi 7 giorni'),
    ('mese', 'Questo mese'),
    ('mese_scorso', 'Mese scorso'),
    ('90g', 'Ultimi 90 giorni'),
]
# Nomi dei periodi della versione precedente della pagina.
ALIAS_PERIODI = {'settimana': '7g', 'trimestre': '90g'}

# Una fase e' fuori scala se dura piu' di FUORI_SCALA volte la mediana della
# sua postazione nel periodo; serve un minimo di fasi per fidarsi della mediana.
FUORI_SCALA = 3
MIN_FASI_MEDIANA = 5

LIMITE_ORDINI = 100


def _data(testo):
    try:
        return date.fromisoformat(testo or '')
    except ValueError:
        return None


def _intervallo(chiave, oggi):
    if chiave == 'oggi':
        return oggi, oggi
    if chiave == '7g':
        return oggi - timedelta(days=6), oggi
    if chiave == '90g':
        return oggi - timedelta(days=89), oggi
    if chiave == 'mese_scorso':
        fine = oggi.replace(day=1) - timedelta(days=1)
        return fine.replace(day=1), fine
    return oggi.replace(day=1), oggi


def periodo(get, oggi=None):
    """(chiave, dal, al) dalla query string; senza indicazioni il mese in corso.

    Date scelte a mano che coincidono con un periodo predefinito prendono il
    suo nome, cosi' il filtro resta evidenziato anche dopo "Applica".
    """
    oggi = oggi or timezone.localdate()
    dal, al = _data(get.get('dal')), _data(get.get('al'))
    if dal or al:
        dal, al = dal or al, al or dal
        if dal > al:
            dal, al = al, dal
        for chiave, _ in PERIODI:
            if _intervallo(chiave, oggi) == (dal, al):
                return chiave, dal, al
        return 'personalizzato', dal, al
    chiave = ALIAS_PERIODI.get(get.get('periodo'), get.get('periodo'))
    if chiave not in dict(PERIODI):
        chiave = 'mese'
    return (chiave, *_intervallo(chiave, oggi))


def nome(utente):
    return utente.get_full_name() or utente.username


def intero(testo):
    try:
        return int(testo)
    except (TypeError, ValueError):
        return None


def report(dal, al, operatore_id=None, postazione_id=None):
    lavorazioni = LavorazioneOperatore.objects.filter(
        stato='completato', inizio__date__range=(dal, al),
    ).select_related('sessione__operatore', 'ordine', 'postazione_cq', 'blocco')
    if operatore_id:
        lavorazioni = lavorazioni.filter(sessione__operatore_id=operatore_id)
    if postazione_id:
        lavorazioni = lavorazioni.filter(postazione_cq_id=postazione_id)

    fasi = []
    for lav in lavorazioni:
        fasi.append({
            'lav': lav,
            'postazione': lav.postazione_cq,
            'operatore': nome(lav.sessione.operatore),
            'minuti': lav.tempo_lavoro_netto_minuti,
            'pausa': lav.tempo_pausa_totale.total_seconds() / 60,
        })

    # Postazioni
    per_post = {}
    for f in fasi:
        per_post.setdefault(f['postazione'].pk, {'postazione': f['postazione'], 'minuti': []})
        per_post[f['postazione'].pk]['minuti'].append(f['minuti'])
    mediane = {}
    postazioni = []
    for pk, d in per_post.items():
        m = d['minuti']
        mediane[pk] = median(m)
        postazioni.append({
            'pk': pk,
            'nome': d['postazione'].nome,
            'sigla': d['postazione'].sigla or d['postazione'].nome[:2].upper(),
            'ordine': d['postazione'].ordine,
            'fasi': len(m),
            'media': sum(m) / len(m),
            'mediana': mediane[pk],
            'minimo': min(m),
            'massimo': max(m),
            'ore': sum(m) / 60,
            'fuori_scala': 0,
        })
    for f in fasi:
        pk = f['postazione'].pk
        f['fuori_scala'] = (len(per_post[pk]['minuti']) >= MIN_FASI_MEDIANA
                            and f['minuti'] > FUORI_SCALA * mediane[pk])
    for p in postazioni:
        p['fuori_scala'] = sum(1 for f in fasi if f['fuori_scala'] and f['postazione'].pk == p['pk'])
    postazioni.sort(key=lambda p: (p['ordine'], p['nome']))
    massimo = max((p['mediana'] for p in postazioni), default=0)
    for p in postazioni:
        p['barra'] = round(p['mediana'] / massimo * 100) if massimo else 0

    # Operatori
    per_op = {}
    for f in fasi:
        d = per_op.setdefault(f['operatore'], {'minuti': [], 'ordini': set()})
        d['minuti'].append(f['minuti'])
        d['ordini'].add(f['lav'].ordine_id)
    ore_totali = sum(f['minuti'] for f in fasi) / 60
    operatori = []
    for chi, d in per_op.items():
        m = d['minuti']
        operatori.append({
            'nome': chi,
            'ordini': len(d['ordini']),
            'fasi': len(m),
            'ore': sum(m) / 60,
            'media': sum(m) / len(m),
            'quota': sum(m) / 60 / ore_totali * 100 if ore_totali else 0,
        })
    operatori.sort(key=lambda o: -o['ore'])
    massimo = max((o['ore'] for o in operatori), default=0)
    for o in operatori:
        o['barra'] = round(o['ore'] / massimo * 100) if massimo else 0

    # Ordini
    per_ordine = {}
    for f in fasi:
        lav = f['lav']
        d = per_ordine.setdefault(lav.ordine_id, {
            'ordine': lav.ordine, 'fasi': [], 'minuti': 0, 'operatori': [], 'inizio': lav.inizio,
        })
        d['fasi'].append(f)
        d['minuti'] += f['minuti']
        d['inizio'] = min(d['inizio'], lav.inizio)
        if f['operatore'] not in d['operatori']:
            d['operatori'].append(f['operatore'])
    ordini = sorted(per_ordine.values(), key=lambda o: o['inizio'], reverse=True)
    for o in ordini:
        o['fasi'].sort(key=lambda f: f['lav'].inizio)
        o['fuori_scala'] = any(f['fuori_scala'] for f in o['fasi'])
        o['operatori'] = ', '.join(sorted(o['operatori']))
    totali = [o['minuti'] for o in ordini]

    in_corso = LavorazioneOperatore.objects.exclude(stato='completato')

    return {
        'n_ordini': len(ordini),
        'n_fasi': len(fasi),
        'media_ordine': sum(totali) / len(totali) if totali else None,
        'mediana_ordine': median(totali) if totali else None,
        'ore': ore_totali,
        'ore_pausa': sum(f['pausa'] for f in fasi) / 60,
        'fuori_scala': sum(1 for f in fasi if f['fuori_scala']),
        'in_corso': in_corso.count(),
        'in_pausa': in_corso.filter(stato='in_pausa').count(),
        'postazioni': postazioni,
        'operatori': operatori,
        'ordini': ordini[:LIMITE_ORDINI],
        'ordini_nascosti': max(0, len(ordini) - LIMITE_ORDINI),
    }


def dettaglio_ordine(ordine):
    """Fasi di un ordine con la posizione sulla linea del tempo (in %)."""
    lavorazioni = list(LavorazioneOperatore.objects.filter(ordine=ordine).select_related(
        'sessione__operatore', 'postazione_cq', 'blocco').order_by('inizio'))
    adesso = timezone.now()
    fasi = []
    for lav in lavorazioni:
        fasi.append({
            'lav': lav,
            'postazione': lav.postazione_cq.nome,
            'sigla': lav.postazione_cq.sigla or lav.postazione_cq.nome[:2].upper(),
            'blocco': lav.blocco.nome if lav.blocco else '',
            'operatore': nome(lav.sessione.operatore),
            'inizio': lav.inizio,
            'fine': lav.fine,
            'in_corso': lav.stato != 'completato',
            'stato': lav.get_stato_display(),
            'pausa': lav.tempo_pausa_totale.total_seconds() / 60,
            'minuti': lav.tempo_lavoro_netto_minuti,
        })
    if not fasi:
        return {'fasi': [], 'minuti': 0, 'pausa': 0, 'durata': None, 'operatori': 0}
    inizio = fasi[0]['inizio']
    fine = max(f['fine'] or adesso for f in fasi)
    durata = (fine - inizio).total_seconds()
    for f in fasi:
        da = (f['inizio'] - inizio).total_seconds()
        a = ((f['fine'] or adesso) - inizio).total_seconds()
        f['sinistra'] = round(da / durata * 100, 2) if durata else 0
        f['larghezza'] = max(round((a - da) / durata * 100, 2), 1.5) if durata else 100
        f['sinistra'] = min(f['sinistra'], round(100 - f['larghezza'], 2))
    return {
        'fasi': fasi,
        'minuti': sum(f['minuti'] for f in fasi),
        'pausa': sum(f['pausa'] for f in fasi),
        'durata': durata / 60,
        'inizio': inizio,
        'fine': fine,
        'operatori': len({f['operatore'] for f in fasi}),
        'aperte': sum(1 for f in fasi if f['in_corso']),
    }
