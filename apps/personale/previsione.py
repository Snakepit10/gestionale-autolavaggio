"""Previsione del lavoro del servito e squadra consigliata per il giorno dopo.

Due passi:
1. un modello (regressione ridge sul logaritmo dei lavaggi, addestrata su
   tutto lo storico degli ordini) stima quanti lavaggi del servito ci
   saranno, da: giorno della settimana, festivi e ponti, stagione, meteo
   del giorno e dei giorni prima, livello delle ultime 4 settimane e
   prenotazioni gia' fatte alle 18 del giorno prima;
2. i lavaggi previsti diventano minuti di lavoro con la durata media dei
   lavaggi da catalogo (somma delle durate dei servizi di ogni ordine), e
   i minuti diventano persone del servito con il tempo produttivo di una
   presenza (RESA_TURNO delle sue ore medie); il self-service ha sempre
   PERSONE_SELF persone in piu'.

Il modello si verifica da solo: addestrato senza gli ultimi GIORNI_VERIFICA
giorni di apertura, prevede quei giorni e confronta con il reale.
Tutto in float, senza dipendenze esterne (numpy non c'e')."""
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

from apps.finanze.lag_analysis import italian_holidays_in_range

INIZIO_ORDINI = date(2025, 8, 1)
GIORNI_VERIFICA = 60
ORA_DECISIONE = time(18, 0)
LAMBDA = 1.0                 # regolarizzazione ridge (sulle variabili standardizzate)
QUANTILE_MARGINE = 0.75      # la squadra si dimensiona sui lavaggi "alti" (75 giorni su 100 ne bastano)
SQUADRA_MINIMA = 1           # persone minime del servito
PERSONE_SELF = 1             # addetti al self-service, sempre presenti, fuori dal calcolo
RESA_TURNO = 0.85            # parte del turno passata davvero sulle auto (il resto: consegne, spostamenti, pulizie)
GIORNI_SETTIMANA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


# ---------------------------------------------------------------- dati

def _meteo(dal, al):
    """{data: {'pioggia': mm, 'tmax': gradi}} da Open-Meteo: archivio fino a
    3 giorni fa, previsioni per gli ultimi giorni e i prossimi 7. None se
    l'API non risponde."""
    from urllib.parse import urlencode

    from apps.finanze.weather import LICATA_LAT, LICATA_LON, _http_get_json
    oggi = timezone.localdate()
    daily = 'precipitation_sum,temperature_2m_max'
    richieste = []
    fine_archivio = min(al, oggi - timedelta(days=3))
    if dal <= fine_archivio:
        richieste.append(('https://archive-api.open-meteo.com/v1/archive',
                          {'start_date': dal.isoformat(), 'end_date': fine_archivio.isoformat()}, 60 * 60 * 24))
    if al > fine_archivio:
        richieste.append(('https://api.open-meteo.com/v1/forecast',
                          {'past_days': 3, 'forecast_days': max(1, min(16, (al - oggi).days + 1))}, 60 * 60))
    meteo = {}
    for url, params, durata in richieste:
        params = {'latitude': LICATA_LAT, 'longitude': LICATA_LON, 'daily': daily, 'timezone': 'Europe/Rome', **params}
        indirizzo = f'{url}?{urlencode(params)}'
        chiave = 'previsione_squadra_meteo_' + str(abs(hash(indirizzo)))
        dati = cache.get(chiave)
        if dati is None:
            dati = _http_get_json(indirizzo, timeout=8.0)
            if not dati or 'daily' not in dati:
                return None
            cache.set(chiave, dati, durata)
        d = dati['daily']
        for i, t in enumerate(d.get('time') or []):
            giorno = date.fromisoformat(t)
            pioggia, tmax = d['precipitation_sum'][i], d['temperature_2m_max'][i]
            if pioggia is not None or giorno not in meteo:
                meteo[giorno] = {'pioggia': pioggia or 0.0, 'tmax': tmax}
    return meteo


def _lavaggi_per_giorno(dal, al):
    from apps.ordini.models import Ordine
    righe = (Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al, vendita_prodotti=False)
             .exclude(stato='annullato').values('data_ora__date').annotate(n=Count('id'), incasso=Sum('totale_finale')))
    return {r['data_ora__date']: (r['n'], float(r['incasso'] or 0)) for r in righe}


def _prenotazioni_note(dal, al):
    """Prenotazioni del servito per giorno, contando solo quelle create
    entro le 18 del giorno prima (quello che si sa quando si decide)."""
    from apps.prenotazioni.models import Prenotazione
    note = defaultdict(int)
    for giorno, creata in (Prenotazione.objects.filter(slot__data__gte=dal, slot__data__lte=al)
                           .exclude(stato='annullata').values_list('slot__data', 'creata_il')):
        limite = timezone.make_aware(datetime.combine(giorno - timedelta(days=1), ORA_DECISIONE))
        if creata <= limite:
            note[giorno] += 1
    return note


def _presenze_per_giorno(dal, al):
    """{data: (ore lavorate, persone)}."""
    from .models import Presenza
    righe = (Presenza.objects.filter(data__gte=dal, data__lte=al, assenza='', ore__gt=0)
             .values('data').annotate(ore=Sum('ore'), persone=Count('id')))
    return {r['data']: (float(r['ore']), r['persone']) for r in righe}


def _costo_orario_per_mese():
    """{primo del mese: costo aziendale per ora lavorata}."""
    from .calcolo import calcola_mese
    from .models import MesePersonale
    costi = {}
    for m in MesePersonale.objects.all():
        r = calcola_mese(m)['riepilogo']
        if r['costo_ora']:
            costi[m.mese] = float(r['costo_ora'])
    return costi


# ---------------------------------------------------------------- variabili

NOMI_VARIABILI = (['Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'] +
                  ['Festivo', 'Prefestivo', 'Postfestivo', 'Stagione (sen)', 'Stagione (cos)',
                   'Pioggia oggi (mm)', 'Pioggia oggi (si/no)', 'Pioggia ieri', 'Pioggia 2-3 giorni fa',
                   'Giorni dall\'ultima pioggia', 'Temperatura massima', 'Livello ultime 4 settimane',
                   'Prenotazioni gia\' fatte'])


def _variabili(giorno, meteo, festivi, livello, prenotazioni):
    wd = giorno.weekday()
    m = meteo.get(giorno, {})
    pioggia = lambda d: (meteo.get(giorno - timedelta(days=d)) or {}).get('pioggia', 0.0)
    asciutto = 0
    while asciutto < 14 and pioggia(asciutto + 1) < 1:
        asciutto += 1
    angolo = 2 * math.pi * giorno.timetuple().tm_yday / 365.25
    tmax = m.get('tmax')
    return ([1.0 if wd == k else 0.0 for k in range(1, 7)] + [
        1.0 if giorno in festivi else 0.0,
        1.0 if giorno + timedelta(days=1) in festivi else 0.0,
        1.0 if giorno - timedelta(days=1) in festivi else 0.0,
        math.sin(angolo), math.cos(angolo),
        math.log1p(m.get('pioggia', 0.0)), 1.0 if m.get('pioggia', 0.0) >= 1 else 0.0,
        math.log1p(pioggia(1)), math.log1p(pioggia(2) + pioggia(3)),
        asciutto / 14,
        tmax if tmax is not None else 22.0,
        livello,
        math.log1p(prenotazioni),
    ])


def _livello(storico, giorno):
    """Media del log dei lavaggi nei giorni di apertura delle 4 settimane
    prima di `giorno` (escluso)."""
    valori = [math.log1p(storico[g][0]) for g in (giorno - timedelta(days=k) for k in range(1, 29)) if g in storico]
    return sum(valori) / len(valori) if valori else None


# ---------------------------------------------------------------- regressione ridge

def _risolvi(a, b):
    """Soluzione di a x = b (eliminazione di Gauss con pivot)."""
    n = len(a)
    m = [riga[:] + [b[i]] for i, riga in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                if f:
                    m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


class Ridge:
    def fit(self, xs, ys):
        p = len(xs[0])
        self.media = [sum(x[j] for x in xs) / len(xs) for j in range(p)]
        self.scarto = [math.sqrt(sum((x[j] - self.media[j]) ** 2 for x in xs) / len(xs)) or 1.0 for j in range(p)]
        zs = [self._z(x) for x in xs]
        self.y0 = sum(ys) / len(ys)
        ata = [[sum(z[i] * z[j] for z in zs) + (LAMBDA if i == j else 0) for j in range(p)] for i in range(p)]
        atb = [sum(z[i] * (y - self.y0) for z, y in zip(zs, ys)) for i in range(p)]
        self.coef = _risolvi(ata, atb)
        return self

    def _z(self, x):
        return [(v - m) / s for v, m, s in zip(x, self.media, self.scarto)]

    def predict(self, x):
        return self.y0 + sum(c * z for c, z in zip(self.coef, self._z(x)))


def _quantile(valori, q):
    if not valori:
        return 0.0
    v = sorted(valori)
    pos = (len(v) - 1) * q
    basso = int(pos)
    alto = min(basso + 1, len(v) - 1)
    return v[basso] + (v[alto] - v[basso]) * (pos - basso)


# ---------------------------------------------------------------- calcolo

def _righe(giorni, storico, meteo, festivi, prenotazioni):
    xs, ys, usati = [], [], []
    for g in giorni:
        livello = _livello(storico, g)
        if livello is None or g not in meteo:
            continue
        xs.append(_variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0)))
        ys.append(math.log1p(storico[g][0]) if g in storico else None)
        usati.append(g)
    return xs, ys, usati


def _minuti_lavaggio(dal, al):
    """Minuti di lavoro medi per lavaggio del servito, dal catalogo: per
    ogni ordine la somma delle durate dei suoi servizi."""
    from django.db.models import F

    from apps.ordini.models import ItemOrdine
    righe = (ItemOrdine.objects.filter(ordine__data_ora__date__gte=dal, ordine__data_ora__date__lte=al,
                                       ordine__vendita_prodotti=False, servizio_prodotto__tipo='servizio')
             .exclude(ordine__stato='annullato')
             .aggregate(minuti=Sum(F('quantita') * F('servizio_prodotto__durata_minuti')), ordini=Count('ordine', distinct=True)))
    return righe['minuti'] / righe['ordini'] if righe['ordini'] else None


def _resa(storico, presenze, ieri):
    """Ore di lavoro per lavaggio (dal catalogo, ultimi 90 giorni) e ore
    medie di una presenza."""
    minuti = _minuti_lavaggio(ieri - timedelta(days=90), ieri)
    giorni = [g for g in presenze if g in storico]
    ore_persona = (sum(presenze[g][0] for g in giorni) / sum(presenze[g][1] for g in giorni)) if giorni else None
    return (minuti / 60 if minuti else None), ore_persona


def squadra_servito(lavaggi, ore_lavaggio, ore_persona):
    if not ore_lavaggio or not ore_persona:
        return None
    return max(SQUADRA_MINIMA, math.ceil(lavaggi * ore_lavaggio / (ore_persona * RESA_TURNO)))


def squadra_per(lavaggi, ore_lavaggio, ore_persona):
    """Squadra totale: servito + self-service."""
    servito = squadra_servito(lavaggi, ore_lavaggio, ore_persona)
    return servito + PERSONE_SELF if servito else None


def calcola(oggi=None):
    """Verifica del modello sugli ultimi giorni e previsione dei prossimi 7."""
    oggi = oggi or timezone.localdate()
    ieri = oggi - timedelta(days=1)
    fine = oggi + timedelta(days=7)
    storico = {g: v for g, v in _lavaggi_per_giorno(INIZIO_ORDINI, ieri).items() if v[0] > 0}
    if len(storico) < 90:
        return {'errore': f'Servono almeno 90 giorni con ordini: ce ne sono {len(storico)}.'}
    meteo = _meteo(INIZIO_ORDINI - timedelta(days=3), fine)
    if meteo is None:
        return {'errore': 'Il servizio meteo (Open-Meteo) non risponde: riprova fra qualche minuto.'}
    festivi = italian_holidays_in_range(INIZIO_ORDINI, fine + timedelta(days=1))
    prenotazioni = _prenotazioni_note(INIZIO_ORDINI, fine)
    presenze = _presenze_per_giorno(INIZIO_ORDINI, ieri)
    ore_lavaggio, ore_persona = _resa(storico, presenze, ieri)
    costo_ora = _costo_orario_per_mese()
    costo_medio = sum(costo_ora.values()) / len(costo_ora) if costo_ora else None

    # giorni di chiusura: giorni della settimana quasi sempre senza ordini
    aperti_wd = defaultdict(int)
    for g in storico:
        aperti_wd[g.weekday()] += 1
    settimane = max(1, (ieri - INIZIO_ORDINI).days / 7)
    chiuso_wd = {wd for wd in range(7) if aperti_wd[wd] < settimane * 0.2}
    # festivi passati senza ordini: chiusi anche quest'anno
    festivi_chiusi = {nome for f, nome in festivi.items() if f <= ieri and f not in storico}

    aperti = sorted(storico)
    xs, ys, usati = _righe(aperti, storico, meteo, festivi, prenotazioni)

    # --- verifica: addestro senza gli ultimi giorni e li prevedo
    taglio = max(len(usati) - GIORNI_VERIFICA, 60)
    verifica = None
    if len(usati) - taglio >= 20:
        modello_v = Ridge().fit(xs[:taglio], ys[:taglio])
        residui_v = [y - modello_v.predict(x) for x, y in zip(xs[:taglio], ys[:taglio])]
        margine_v = _quantile(residui_v, QUANTILE_MARGINE)
        giorni_v = []
        for x, y, g in zip(xs[taglio:], ys[taglio:], usati[taglio:]):
            previsti = max(0.0, math.expm1(modello_v.predict(x)))
            reali = storico[g][0]
            riga = {'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'previsti': previsti, 'reali': reali,
                    'pioggia': meteo[g]['pioggia'], 'scarto_pct': (previsti - reali) / reali * 100}
            riga['squadra'] = squadra_per(math.expm1(modello_v.predict(x) + margine_v), ore_lavaggio, ore_persona)
            riga['squadra_ideale'] = squadra_per(reali, ore_lavaggio, ore_persona)
            if g in presenze:
                riga['persone'], riga['ore'] = presenze[g][1], presenze[g][0]
                if riga['squadra'] and ore_persona:
                    riga['ore_risparmiate'] = riga['ore'] - riga['squadra'] * ore_persona
                    c = costo_ora.get(g.replace(day=1), costo_medio)
                    riga['risparmio'] = riga['ore_risparmiate'] * c if c else None
            giorni_v.append(riga)
        errori = [abs(r['previsti'] - r['reali']) for r in giorni_v]
        con_squadra = [r for r in giorni_v if r['squadra'] and r['squadra_ideale']]
        con_presenze = [r for r in giorni_v if 'persone' in r]
        media_reali = sum(r['reali'] for r in giorni_v) / len(giorni_v)
        base = [abs(r['reali'] - _media_wd(storico, r['data'], usati[:taglio])) for r in giorni_v]
        verifica = {
            'giorni': giorni_v, 'dal': giorni_v[0]['data'], 'al': giorni_v[-1]['data'],
            'errore_medio': sum(errori) / len(errori), 'errore_pct': sum(errori) / len(errori) / media_reali * 100,
            'errore_base_pct': sum(base) / len(base) / media_reali * 100,
            'entro_20': sum(1 for r in giorni_v if abs(r['scarto_pct']) <= 20) / len(giorni_v) * 100,
            'squadra_giusta': (sum(1 for r in con_squadra if r['squadra'] == r['squadra_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'squadra_scarsa': (sum(1 for r in con_squadra if r['squadra'] < r['squadra_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'giorni_presenze': len(con_presenze),
            'ore_risparmiate': sum(r.get('ore_risparmiate', 0) for r in con_presenze),
            'risparmio': sum(r.get('risparmio') or 0 for r in con_presenze),
        }

    # --- modello finale su tutto lo storico
    modello = Ridge().fit(xs, ys)
    residui = [y - modello.predict(x) for x, y in zip(xs, ys)]
    margine = _quantile(residui, QUANTILE_MARGINE)
    previsioni = []
    storico_esteso = dict(storico)
    finora = _lavaggi_per_giorno(oggi, oggi).get(oggi, (0, 0))[0]
    for k in range(0, 8):
        g = oggi + timedelta(days=k)
        if g not in meteo:
            continue
        chiuso = g.weekday() in chiuso_wd or festivi.get(g) in festivi_chiusi
        livello = _livello(storico_esteso, g) or _livello(storico, oggi)
        note = _prenotazioni_ora(g)
        # oggi: le prenotazioni note alle 18 di ieri, come nell'addestramento
        x = _variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0) if k == 0 else note)
        stima = modello.predict(x)
        previsti, alti = max(0.0, math.expm1(stima)), max(0.0, math.expm1(stima + margine))
        previsioni.append({
            'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'chiuso': chiuso, 'domani': k == 1,
            'oggi': k == 0, 'finora': finora if k == 0 else None,
            'pioggia': meteo[g]['pioggia'], 'tmax': meteo[g]['tmax'], 'prenotazioni': note,
            'previsti': previsti, 'alti': alti,
            'ore': alti * ore_lavaggio if ore_lavaggio else None,
            'squadra': None if chiuso else squadra_per(alti, ore_lavaggio, ore_persona),
            'servito': None if chiuso else squadra_servito(alti, ore_lavaggio, ore_persona),
        })
        if k > 0:
            storico_esteso[g] = (round(previsti), 0)

    pesi = sorted(((n, c) for n, c in zip(NOMI_VARIABILI, modello.coef)), key=lambda t: -abs(t[1]))
    return {
        'oggi': oggi, 'giorni_storico': len(usati), 'dal': usati[0], 'previsioni': previsioni, 'verifica': verifica,
        'ore_lavaggio': ore_lavaggio, 'minuti_lavaggio': ore_lavaggio * 60 if ore_lavaggio else None,
        'ore_persona': ore_persona, 'ore_produttive': ore_persona * RESA_TURNO if ore_persona else None,
        'resa_turno': RESA_TURNO * 100, 'persone_self': PERSONE_SELF,
        'esempio_20': squadra_servito(20, ore_lavaggio, ore_persona), 'giorni_presenze': len(presenze),
        'costo_ora': costo_medio, 'pesi': [{'nome': n, 'effetto': (math.exp(c) - 1) * 100} for n, c in pesi[:8]],
        'margine_pct': (math.exp(margine) - 1) * 100,
        'chiuso': [GIORNI_SETTIMANA[wd] for wd in sorted(chiuso_wd)],
    }


def _prenotazioni_ora(giorno):
    from apps.prenotazioni.models import Prenotazione
    return Prenotazione.objects.filter(slot__data=giorno).exclude(stato='annullata').count()


def _media_wd(storico, giorno, giorni):
    """Previsione "ingenua" di confronto: media dei lavaggi dello stesso
    giorno della settimana nello storico di addestramento."""
    valori = [storico[g][0] for g in giorni if g.weekday() == giorno.weekday()]
    return sum(valori) / len(valori) if valori else 0


def registra(risultato):
    """Salva la previsione di domani (dalle 18 in poi), per confrontarla
    poi con quanto e' successo."""
    from .models import PrevisioneSquadra
    domani = next((p for p in risultato.get('previsioni', []) if p['domani']), None)
    if not domani or timezone.localtime().time() < ORA_DECISIONE:
        return None
    return PrevisioneSquadra.objects.update_or_create(data=domani['data'], defaults={
        'lavaggi_previsti': round(domani['previsti'], 1), 'lavaggi_alti': round(domani['alti'], 1),
        'squadra': domani['squadra'] or 0, 'pioggia_mm': domani['pioggia'], 'chiuso': domani['chiuso'],
    })[0]


def storico_previsioni(giorni=30):
    """Previsioni salvate dei giorni passati, con lavaggi e persone reali."""
    from .models import PrevisioneSquadra
    oggi = timezone.localdate()
    salvate = list(PrevisioneSquadra.objects.filter(data__lt=oggi, data__gte=oggi - timedelta(days=giorni)).order_by('-data'))
    if not salvate:
        return []
    reali = _lavaggi_per_giorno(salvate[-1].data, salvate[0].data)
    presenze = _presenze_per_giorno(salvate[-1].data, salvate[0].data)
    return [{'p': p, 'reali': reali.get(p.data, (0, 0))[0], 'persone': presenze.get(p.data, (0, 0))[1]} for p in salvate]
