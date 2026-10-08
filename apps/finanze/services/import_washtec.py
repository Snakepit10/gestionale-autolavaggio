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
- il portale (A/B) si deduce dal contatore: ogni transazione va al
  portale del numero gia' noto piu' vicino (archivio + righe gia'
  classificate), perche' ogni portale ha un contatore progressivo suo;
- si tengono solo manutenzione = No ed eseguito = Si'.
"""
import bisect
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.finanze.models import TransazionePortale

METODO_ORIGINE = {
    'in contanti': 'unita',
    'unità operativa parallela': 'contanti',
    'unita operativa parallela': 'contanti',
}
# Oltre questa distanza dal numero noto piu' vicino il portale e'
# ambiguo: la riga viene segnalata invece che indovinata.
DISTANZA_MAX = 300


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

    noti = dict(TransazionePortale.objects.values_list('numero', 'portale'))
    if not noti:
        noti = _assegna_portali_senza_archivio(c['numero'] for c in candidati)
    chiavi = sorted(noti)

    transazioni = []
    for c in sorted(candidati, key=lambda c: c['numero']):
        n = c['numero']
        i = bisect.bisect_left(chiavi, n)
        vicini = [chiavi[j] for j in (i - 1, i) if 0 <= j < len(chiavi)]
        if not vicini:
            anomalie.append(f'#{n}: impossibile dedurre il portale')
            continue
        vicino = min(vicini, key=lambda k: abs(k - n))
        if abs(vicino - n) > DISTANZA_MAX:
            anomalie.append(f'#{n} {c["orario"]:%d/%m %H:%M}: portale ambiguo '
                            f'(numero noto piu\' vicino {vicino})')
            continue
        portale = noti[vicino]
        if n not in noti:
            noti[n] = portale
            bisect.insort(chiavi, n)
        transazioni.append({**c, 'portale': portale})

    return {'transazioni': transazioni, 'jetwash': jetwash,
            'escluse': escluse, 'anomalie': anomalie}


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
