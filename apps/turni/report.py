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

# Due fasi dello stesso operatore, ordine e postazione con meno di questo
# stacco sono una fase sola (chiusa e riavviata subito).
STACCO_MAX = timedelta(minutes=2)


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


def unisci_blocchi(lavorazioni, adesso=None):
    """Una fase per operatore, ordine e postazione, anche con piu' blocchi.

    "Avvia" crea una riga per ogni blocco scelto dall'operatore a inizio
    turno, con gli stessi orari: e' lo stesso lavoro della stessa persona.
    Le righe della stessa sessione, ordine e postazione con orari
    sovrapposti diventano una fase sola, con il tempo della piu' lunga.
    Fasi chiuse e riavviate entro STACCO_MAX si uniscono anche loro; la
    postazione rifatta piu' tardi resta una fase separata.
    """
    adesso = adesso or timezone.now()
    righe = sorted(lavorazioni, key=lambda l: (l.sessione_id, l.ordine_id, l.postazione_cq_id, l.inizio))
    fasi = []
    for lav in righe:
        chiave = (lav.sessione_id, lav.ordine_id, lav.postazione_cq_id)
        fine = lav.fine or adesso
        ultima = fasi[-1] if fasi else None
        if ultima and ultima['chiave'] == chiave and lav.inizio <= ultima['fine_gruppo']:
            ultima['fine_gruppo'] = max(ultima['fine_gruppo'], fine)
            ultima['minuti'] = max(ultima['minuti'], lav.tempo_lavoro_netto_minuti)
            ultima['pausa'] = max(ultima['pausa'], lav.tempo_pausa_totale.total_seconds() / 60)
            ultima['in_corso'] = ultima['in_corso'] or lav.stato != 'completato'
            ultima['in_pausa'] = ultima['in_pausa'] or lav.stato == 'in_pausa'
            if lav.fine and ultima['fine'] and lav.fine > ultima['fine']:
                ultima['fine'] = lav.fine
            if lav.blocco:
                ultima['blocchi'].append(lav.blocco.nome)
            ultima['ids'].append(lav.pk)
            continue
        fasi.append({
            'chiave': chiave,
            'lav': lav,
            'postazione': lav.postazione_cq,
            'operatore': nome(lav.sessione.operatore),
            'inizio': lav.inizio,
            'fine': lav.fine,
            'fine_gruppo': fine,
            'in_corso': lav.stato != 'completato',
            'in_pausa': lav.stato == 'in_pausa',
            'stato': lav.get_stato_display(),
            'minuti': lav.tempo_lavoro_netto_minuti,
            'pausa': lav.tempo_pausa_totale.total_seconds() / 60,
            'blocchi': [lav.blocco.nome] if lav.blocco else [],
            'ids': [lav.pk],
        })
    # Fasi chiuse e riavviate subito (un "Fine" premuto troppo presto):
    # con meno di STACCO_MAX fra una e l'altra sono la stessa fase; i minuti
    # netti si sommano e lo stacco conta come pausa.
    unite = []
    for f in fasi:
        prima = unite[-1] if unite else None
        if prima and prima['chiave'] == f['chiave'] and f['inizio'] - prima['fine_gruppo'] <= STACCO_MAX:
            stacco = (f['inizio'] - prima['fine_gruppo']).total_seconds() / 60
            prima['minuti'] += f['minuti']
            prima['pausa'] += f['pausa'] + stacco
            prima['fine_gruppo'] = f['fine_gruppo']
            prima['fine'] = f['fine']
            prima['in_corso'] = prima['in_corso'] or f['in_corso']
            prima['in_pausa'] = prima['in_pausa'] or f['in_pausa']
            prima['blocchi'] += [b for b in f['blocchi'] if b not in prima['blocchi']]
            prima['riprese'] += 1
            prima['ids'] += f['ids']
            continue
        f['riprese'] = 0
        unite.append(f)
    for f in unite:
        if f['in_corso']:
            f['fine'] = None
            f['stato'] = 'In pausa' if f['in_pausa'] else 'In corso'
    unite.sort(key=lambda f: f['inizio'])
    return unite


def report(dal, al, operatore_id=None, postazione_id=None):
    lavorazioni = LavorazioneOperatore.objects.filter(
        stato='completato', inizio__date__range=(dal, al),
    ).select_related('sessione__operatore', 'ordine', 'postazione_cq', 'blocco')
    if operatore_id:
        lavorazioni = lavorazioni.filter(sessione__operatore_id=operatore_id)
    if postazione_id:
        lavorazioni = lavorazioni.filter(postazione_cq_id=postazione_id)

    fasi = unisci_blocchi(lavorazioni)

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

    attivi = in_lavorazione()
    aperte = [f for o in attivi for f in o['fasi'] if f['in_corso']]

    return {
        'n_ordini': len(ordini),
        'n_fasi': len(fasi),
        'media_ordine': sum(totali) / len(totali) if totali else None,
        'mediana_ordine': median(totali) if totali else None,
        'ore': ore_totali,
        'ore_pausa': sum(f['pausa'] for f in fasi) / 60,
        'fuori_scala': sum(1 for f in fasi if f['fuori_scala']),
        'in_corso': len(aperte),
        'in_pausa': sum(1 for f in aperte if f['in_pausa']),
        'in_lavorazione': attivi,
        'postazioni': postazioni,
        'operatori': operatori,
        'ordini': ordini[:LIMITE_ORDINI],
        'ordini_nascosti': max(0, len(ordini) - LIMITE_ORDINI),
    }


def in_lavorazione():
    """Ordini con almeno una fase aperta adesso, con tutte le loro fasi.

    I minuti delle fasi aperte arrivano fino a questo momento.
    """
    adesso = timezone.now()
    ordini_ids = (LavorazioneOperatore.objects.exclude(stato='completato')
                  .values_list('ordine_id', flat=True).distinct())
    righe = LavorazioneOperatore.objects.filter(ordine_id__in=list(ordini_ids)).select_related(
        'sessione__operatore', 'ordine', 'postazione_cq', 'blocco')
    per_ordine = {}
    for f in unisci_blocchi(righe, adesso):
        d = per_ordine.setdefault(f['lav'].ordine_id, {'ordine': f['lav'].ordine, 'fasi': []})
        d['fasi'].append(f)
    ordini = []
    for d in per_ordine.values():
        fasi = d['fasi']
        inizio = min(f['inizio'] for f in fasi)
        d.update({
            'inizio': inizio,
            'trascorsi': (adesso - inizio).total_seconds() / 60,
            'minuti': sum(f['minuti'] for f in fasi),
            'aperte': [f for f in fasi if f['in_corso']],
            'operatori': ', '.join(sorted({f['operatore'] for f in fasi if f['in_corso']})),
        })
        ordini.append(d)
    ordini.sort(key=lambda o: o['inizio'])
    return ordini


def dettaglio_ordine(ordine):
    """Fasi di un ordine con la posizione sulla linea del tempo (in %)."""
    lavorazioni = list(LavorazioneOperatore.objects.filter(ordine=ordine).select_related(
        'sessione__operatore', 'postazione_cq', 'blocco').order_by('inizio'))
    adesso = timezone.now()
    fasi = unisci_blocchi(lavorazioni, adesso)
    for f in fasi:
        p = f['postazione']
        f['sigla'] = p.sigla or p.nome[:2].upper()
        f['postazione'] = p.nome
        f['blocco'] = ', '.join(f['blocchi'])
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
