"""Classificazione delle righe grezze di WashTec Plus (Report > Dati
transazione) in TransazionePortale.

Le righe arrivano dal bookmarklet cosi' come appaiono in tabella:
[numero, orario 'gg/mm/aa HH:MM:SS', programma, metodo, pagato,
manutenzione, eseguito]. Tutte le regole stanno qui, lato server, cosi'
un cambio non richiede di reinstallare il bookmarklet.

Regole (vedi anche le note su TransazionePortale):
- portali a spazzole = importo 0,00 e metodo "In contanti" /
  "Unita' operativa parallela"; il resto con importo > 0 e' JetWash
  self-service (escluso);
- etichette WashTec INVERTITE: "In contanti" = unita' operativa,
  "Unita' operativa parallela" = contanti;
- il portale (A/B) si deduce dal contatore: ogni portale ha un contatore
  progressivo suo, quindi in ordine di tempo le transazioni formano due
  "catene" di numeri consecutivi. Il numero da solo non basta (a
  settembre 2026 B usava i numeri 13121-13864 che a ottobre usa A): una
  catena prende il portale delle transazioni gia' in archivio che
  contiene, altrimenti quello compatibile nel tempo (il contatore non
  torna mai indietro) o l'opposto della catena dell'altro portale che
  corre negli stessi giorni;
- si tengono solo manutenzione = No ed eseguito = Si'.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.finanze.models import TransazionePortale

METODO_ORIGINE = {
    'in contanti': 'unita',
    'unità operativa parallela': 'contanti',
    'unita operativa parallela': 'contanti',
}
# Salto massimo tra due numeri consecutivi della stessa catena (righe
# escluse o mancanti in mezzo): oltre si apre una catena nuova.
SALTO_CATENA = 50
# Lavaggi al giorno oltre i quali un portale non puo' arrivare: limita
# quanto il contatore puo' essere avanzato tra due transazioni note.
LAVAGGI_GIORNO_MAX = 150


def _salto_possibile(da, a):
    return SALTO_CATENA + LAVAGGI_GIORNO_MAX * abs((a - da).total_seconds()) / 86400


def _importo(testo):
    t = (testo or '').replace('EUR', '').replace('€', '').strip()
    t = t.replace('.', '').replace(',', '.')
    try:
        return Decimal(t or '0')
    except InvalidOperation:
        return None


def _orario(testo):
    return timezone.make_aware(
        datetime.strptime(testo.strip(), '%d/%m/%y %H:%M:%S'))


def _assegna_portali_senza_archivio(numeri):
    """Primo import ad archivio vuoto: due gruppi separati dal salto
    piu' ampio tra numeri consecutivi (A = gruppo basso)."""
    numeri = sorted(set(numeri))
    if len(numeri) < 2:
        return {n: 'A' for n in numeri}
    salti = [(numeri[i + 1] - numeri[i], i) for i in range(len(numeri) - 1)]
    _, taglio = max(salti)
    return {n: ('A' if i <= taglio else 'B') for i, n in enumerate(numeri)}


def classifica(righe):
    """Ritorna {'transazioni': [...dict TransazionePortale...],
    'jetwash': int, 'escluse': [...], 'anomalie': [...]}."""
    candidati, anomalie, escluse = [], [], []
    jetwash = 0
    for r in righe:
        try:
            numero = int(str(r[0]).strip())
            orario = _orario(r[1])
            programma = int(str(r[2]).strip())
        except (ValueError, TypeError, IndexError):
            anomalie.append(f'riga illeggibile: {r}')
            continue
        metodo = str(r[3]).strip().lower()
        importo = _importo(r[4])
        manut, eseguito = str(r[5]).strip(), str(r[6]).strip()

        origine = METODO_ORIGINE.get(metodo)
        if origine is None or importo != 0:
            if importo and importo > 0:
                jetwash += 1          # self-service a pagamento
            else:
                anomalie.append(f'#{numero} {r[1]}: metodo "{r[3]}" importo {r[4]}')
            continue
        if manut != 'No' or eseguito not in ('Sì', 'Si', 'Sí'):
            escluse.append(f'#{numero} {r[1]}: manutenzione {manut}, eseguito {eseguito}')
            continue
        if not 1 <= programma <= 9:
            anomalie.append(f'#{numero} {r[1]}: programma {programma}')
            continue
        candidati.append({'numero': numero, 'orario': orario,
                          'programma': programma, 'origine': origine})

    noti = list(TransazionePortale.objects.values_list('numero', 'orario', 'portale'))
    if noti:
        portali = _assegna_portali(candidati, noti)
    else:
        primo = _assegna_portali_senza_archivio(c['numero'] for c in candidati)
        portali = [primo[c['numero']] for c in candidati]

    transazioni = []
    for c, portale in zip(candidati, portali):
        if portale is None:
            anomalie.append(f'#{c["numero"]} {timezone.localtime(c["orario"]):%d/%m %H:%M}: '
                            'portale ambiguo')
            continue
        transazioni.append({**c, 'portale': portale})
    transazioni.sort(key=lambda t: t['numero'])

    return {'transazioni': transazioni, 'jetwash': jetwash,
            'escluse': escluse, 'anomalie': anomalie}


def _catene(punti):
    """Raggruppa i punti (ordinati per orario) in catene di numeri
    crescenti: ogni punto si aggancia alla catena il cui ultimo numero lo
    precede col salto piu' piccolo (al massimo SALTO_CATENA)."""
    catene = []
    for p in punti:
        migliore = None
        for c in catene:
            salto = p['numero'] - c['punti'][-1]['numero']
            if 0 < salto <= SALTO_CATENA and (
                    migliore is None or salto < p['numero'] - migliore['punti'][-1]['numero']):
                migliore = c
        if migliore is None:
            migliore = {'punti': []}
            catene.append(migliore)
        migliore['punti'].append(p)
    for c in catene:
        c['t0'], c['t1'] = c['punti'][0]['orario'], c['punti'][-1]['orario']
        c['n0'], c['n1'] = c['punti'][0]['numero'], c['punti'][-1]['numero']
        etichette = {p['portale'] for p in c['punti'] if p['portale']}
        c['portale'] = etichette.pop() if len(etichette) == 1 else None
        c['conflitto'] = len(etichette) > 0
    return catene


def _compatibile(catena, portale, catene):
    """Il contatore non torna indietro e non corre troppo: le transazioni
    note del portale prima della catena hanno numeri piu' bassi, quelle
    dopo piu' alti, entrambe a una distanza raggiungibile nel tempo."""
    for c in catene:
        if c['portale'] != portale:
            continue
        for p in c['punti']:
            if p['orario'] < catena['t0'] and not (
                    0 < catena['n0'] - p['numero'] <= _salto_possibile(p['orario'], catena['t0'])):
                return False
            if p['orario'] > catena['t1'] and not (
                    0 < p['numero'] - catena['n1'] <= _salto_possibile(catena['t1'], p['orario'])):
                return False
            if catena['t0'] <= p['orario'] <= catena['t1'] and not (
                    catena['n0'] < p['numero'] < catena['n1']):
                return False
    return True


def _spareggio(da_etichettare, catene):
    """Due catene degli stessi giorni compatibili entrambe con A e B: i
    due contatori avanzano a ritmi simili, quindi mantengono l'ordine
    che hanno in archivio (la catena piu' bassa va al portale che in
    archivio ha i numeri piu' bassi)."""
    ultimo = {}
    for c in catene:
        if c['portale'] and (c['portale'] not in ultimo or c['t1'] > ultimo[c['portale']]['t1']):
            ultimo[c['portale']] = c
    if len(ultimo) < 2:
        return False
    basso = 'A' if ultimo['A']['n1'] < ultimo['B']['n1'] else 'B'
    alto = 'B' if basso == 'A' else 'A'
    for c in da_etichettare:
        for d in da_etichettare:
            if d is c or not (d['t0'] <= c['t1'] and c['t0'] <= d['t1']):
                continue
            if not all(_compatibile(x, p, catene) for x in (c, d) for p in ('A', 'B')):
                continue
            inferiore, superiore = (c, d) if c['n0'] < d['n0'] else (d, c)
            for catena, portale in ((inferiore, basso), (superiore, alto)):
                catena['portale'] = portale
                for p in catena['punti']:
                    p['portale'] = portale
                da_etichettare.remove(catena)
            return True
    return False


def _assegna_portali(candidati, noti):
    """Portale ('A'/'B' o None se ambiguo) per ogni candidato, nello
    stesso ordine, a partire dalle transazioni gia' in archivio."""
    etichetta = {(n, o): portale for n, o, portale in noti}
    punti = [{'numero': n, 'orario': o, 'portale': portale} for n, o, portale in noti]
    for c in candidati:
        if (c['numero'], c['orario']) not in etichetta:
            punti.append({'numero': c['numero'], 'orario': c['orario'], 'portale': None})
    punti.sort(key=lambda p: (p['orario'], p['numero']))
    catene = _catene(punti)
    da_etichettare = [c for c in catene if c['portale'] is None and not c['conflitto']]

    cambiato = True
    while cambiato and da_etichettare:
        cambiato = False
        for c in list(da_etichettare):
            possibili = [p for p in ('A', 'B') if _compatibile(c, p, catene)]
            if len(possibili) == 2:
                # due portali, due contatori: una catena che corre negli
                # stessi giorni di una gia' etichettata e' l'altro portale
                vicine = {x['portale'] for x in catene if x is not c and x['portale']
                          and x['t0'] <= c['t1'] and c['t0'] <= x['t1']}
                if len(vicine) == 1:
                    possibili = [p for p in possibili if p not in vicine]
            if len(possibili) == 1:
                c['portale'] = possibili[0]
                for p in c['punti']:
                    p['portale'] = c['portale']
                da_etichettare.remove(c)
                cambiato = True
        if not cambiato:
            cambiato = _spareggio(da_etichettare, catene)

    for c in catene:
        for p in c['punti']:
            if p['portale'] is None:
                p['portale'] = c['portale']
            etichetta.setdefault((p['numero'], p['orario']), p['portale'])
    return [etichetta.get((c['numero'], c['orario'])) for c in candidati]


def importa(righe, conferma=False):
    """Classifica e (se conferma) salva in archivio. Idempotente."""
    esito = classifica(righe)
    trans = esito['transazioni']
    esistenti = set(TransazionePortale.objects.filter(
        numero__in=[t['numero'] for t in trans]).values_list('portale', 'numero'))
    nuove = [t for t in trans if (t['portale'], t['numero']) not in esistenti]

    per_giorno = {}
    for t in nuove:
        g = timezone.localtime(t['orario']).date()
        cella = per_giorno.setdefault(g, {'A': 0, 'B': 0})
        cella[t['portale']] += 1

    if conferma and nuove:
        TransazionePortale.objects.bulk_create(
            [TransazionePortale(**t) for t in nuove], ignore_conflicts=True)

    return {
        'righe': len(righe),
        'portali': len(trans),
        'nuove': len(nuove),
        'gia_presenti': len(trans) - len(nuove),
        'jetwash': esito['jetwash'],
        'escluse': esito['escluse'],
        'anomalie': esito['anomalie'],
        'per_giorno': [{'data': g.isoformat(), 'A': v['A'], 'B': v['B']}
                       for g, v in sorted(per_giorno.items())],
        'importato': bool(conferma),
        'totale_archivio': TransazionePortale.objects.count(),
    }


def buchi_archivio(da, a, margine=timedelta(days=2)):
    """Lavaggi mancanti nell'archivio tra da e a: ogni portale numera le
    sue transazioni senza salti, quindi un numero che manca tra due
    transazioni importate e' un lavaggio non importato (o una riga
    scartata dall'import: manutenzione, non eseguita). Ritorna
    [{'portale', 'da_numero', 'a_numero', 'quanti', 'dopo', 'prima'}]."""
    buchi = []
    for portale, _ in TransazionePortale.PORTALE_CHOICES:
        righe = list(TransazionePortale.objects
                     .filter(portale=portale, orario__gte=da - margine, orario__lte=a + margine)
                     .order_by('numero').values_list('numero', 'orario'))
        for (n1, t1), (n2, t2) in zip(righe, righe[1:]):
            if 1 < n2 - n1 <= SALTO_CATENA and t2 > da and t1 < a:
                buchi.append({'portale': portale, 'da_numero': n1 + 1, 'a_numero': n2 - 1,
                              'quanti': n2 - n1 - 1, 'dopo': t1, 'prima': t2})
    return buchi
