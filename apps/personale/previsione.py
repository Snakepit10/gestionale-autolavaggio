"""Previsione del lavoro del servito e squadra consigliata per il giorno dopo.

Due passi:
1. un modello (regressione ridge sul logaritmo dei lavaggi, addestrata su
   tutto lo storico degli ordini) stima quanti lavaggi del servito ci
   saranno, da: giorno della settimana, festivi e ponti, stagione, meteo
   del giorno e dei giorni prima, livello delle ultime 4 settimane e
   prenotazioni gia' fatte alle 18 del giorno prima;
2. persone al servito = lavaggi previsti / lavaggi che una persona fa in
   una giornata (parametro, di partenza 7: 20 lavaggi = 3 persone);
3. un secondo modello, con le stesse variabili, stima i cicli self delle
   spazzole (cicli dei portali meno quelli avviati per il servito): sopra
   la soglia impostata serve una persona in piu' per il self.
Parametri in ParametriPrevisione, modificabili dalla pagina.

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
LAMBDE = (1, 3, 10, 30, 100)  # regolarizzazione ridge provata: si tiene quella che sbaglia meno
OBIETTIVO_SCOPERTI = 0.10     # margine piu' piccolo che lascia scoperti al massimo 1 giorno su 10
GIORNI_TARATURA = 60          # giorni (gli ultimi dell'addestramento) su cui si sceglie regolarizzazione e margine
SQUADRA_MINIMA = 1           # persone minime del servito
# modelli meteo di Open-Meteo, in ordine di preferenza: ItaliaMeteo-ARPAE ICON 2I
# (2,2 km sull'Italia, ~3 giorni), poi la scelta automatica per i giorni che non copre
MODELLI_METEO = ('italia_meteo_arpae_icon_2i', 'best_match')
NOMI_MODELLI = {'italia_meteo_arpae_icon_2i': 'ItaliaMeteo ICON-2I', 'best_match': 'Open-Meteo'}
GIORNI_SETTIMANA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


# ---------------------------------------------------------------- dati

CODICI_PIOGGIA = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))   # WMO: pioviggine, pioggia, rovesci, temporali


def piove(m):
    """Giornata di pioggia: almeno 1 mm, oppure 2 ore di pioggia, oppure
    un codice meteo di pioggia, oppure (nelle previsioni) probabilita'
    almeno 60%. Anche una pioggia debole per tutto il pomeriggio conta,
    anche se i millimetri sono pochi."""
    return bool(m) and (m.get('pioggia', 0) >= 1 or m.get('ore', 0) >= 2 or m.get('codice') in CODICI_PIOGGIA
                        or (m.get('probabilita') or 0) >= 60)


def _meteo(dal, al):
    """{data: {'pioggia': mm, 'ore': ore di pioggia, 'codice': codice WMO,
    'probabilita': % (solo previsioni), 'tmax': gradi, 'piove': bool}} da
    Open-Meteo: archivio fino a 3 giorni fa, previsioni per gli ultimi
    giorni e i prossimi 7. None se l'API non risponde."""
    from apps.finanze.weather import LICATA_LAT, LICATA_LON
    oggi = timezone.localdate()
    daily = 'precipitation_sum,precipitation_hours,weather_code,temperature_2m_max'
    richieste = []
    fine_archivio = min(al, oggi - timedelta(days=3))
    if dal <= fine_archivio:
        richieste.append(('https://archive-api.open-meteo.com/v1/archive', daily,
                          {'start_date': dal.isoformat(), 'end_date': fine_archivio.isoformat()}, 60 * 60 * 24))
    if al > fine_archivio:
        richieste.append(('https://api.open-meteo.com/v1/forecast', daily + ',precipitation_probability_max',
                          {'past_days': 3, 'forecast_days': max(1, min(16, (al - oggi).days + 1))}, 60 * 30))
    meteo = {}
    for url, variabili, params, durata in richieste:
        params = {'latitude': LICATA_LAT, 'longitude': LICATA_LON, 'daily': variabili, 'timezone': 'Europe/Rome', **params}
        dati = _scarica(url, params, durata, modelli='forecast' in url)
        if not dati:
            return None
        _giorni_meteo(dati['daily'], meteo)
    return meteo


def _scarica(url, params, durata, modelli=True, chiave_dati='daily'):
    """JSON di Open-Meteo (con cache): prima con i MODELLI_METEO, se la
    richiesta fallisce senza (modello automatico)."""
    import hashlib
    from urllib.parse import urlencode

    from apps.finanze.weather import _http_get_json
    for extra in (({'models': ','.join(MODELLI_METEO)},) if modelli else ()) + ({},):
        indirizzo = f'{url}?{urlencode({**params, **extra})}'
        chiave = 'previsione_squadra_meteo_' + hashlib.md5(indirizzo.encode()).hexdigest()
        dati = cache.get(chiave)
        if dati is None:
            dati = _http_get_json(indirizzo, timeout=10.0)
            if dati and chiave_dati in dati:
                cache.set(chiave, dati, durata)
        if dati and chiave_dati in dati:
            return dati
    return None


def _meteo_previsto_storico(dal, al):
    """Le previsioni di allora (Open-Meteo Historical Forecast): quello che
    i clienti vedevano sul telefono, anche quando poi non ha piovuto.
    Stessa forma di _meteo; None se l'API non risponde."""
    from apps.finanze.weather import LICATA_LAT, LICATA_LON
    base = 'precipitation_sum,precipitation_hours,weather_code,temperature_2m_max'
    for variabili in (base + ',precipitation_probability_max', base):    # la probabilita' puo' mancare
        params = {'latitude': LICATA_LAT, 'longitude': LICATA_LON, 'daily': variabili, 'timezone': 'Europe/Rome',
                  'start_date': dal.isoformat(), 'end_date': al.isoformat()}
        dati = _scarica('https://historical-forecast-api.open-meteo.com/v1/forecast', params, 60 * 60 * 12)
        if dati:
            return _giorni_meteo(dati['daily'], {})
    return None


def _giorni_meteo(d, meteo):
    """Con piu' modelli le serie hanno il suffisso del modello: per ogni
    variabile si prende il primo modello che ha il dato."""
    def valore(nome, i):
        for chiave in [f'{nome}_{m}' for m in MODELLI_METEO] + [nome]:
            serie = d.get(chiave)
            if serie and i < len(serie) and serie[i] is not None:
                return serie[i]
        return None

    def fonte(i):
        for m in MODELLI_METEO:
            serie = d.get(f'precipitation_sum_{m}')
            if serie and i < len(serie) and serie[i] is not None:
                return NOMI_MODELLI[m]
        return 'Open-Meteo'

    for i, t in enumerate(d.get('time') or []):
        giorno = date.fromisoformat(t)
        pioggia = valore('precipitation_sum', i)
        if pioggia is None and giorno in meteo:
            continue
        if pioggia is None and valore('weather_code', i) is None:
            continue                                   # giorno senza dati
        m = {'pioggia': pioggia or 0.0, 'ore': valore('precipitation_hours', i) or 0.0,
             'codice': valore('weather_code', i), 'probabilita': valore('precipitation_probability_max', i),
             'tmax': valore('temperature_2m_max', i), 'fonte': fonte(i)}
        m['piove'] = piove(m)
        meteo[giorno] = m
    return meteo


def _lavaggi_per_giorno(dal, al):
    from apps.ordini.models import Ordine
    righe = (Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al, vendita_prodotti=False)
             .exclude(stato='annullato').values('data_ora__date').annotate(n=Count('id'), incasso=Sum('totale_finale')))
    return {r['data_ora__date']: (r['n'], float(r['incasso'] or 0)) for r in righe}


def _cicli_self_per_giorno(dal, al):
    """Cicli self delle spazzole per giorno. Dalle transazioni WashTec
    pagate al portale dal cliente (le altre le avvia l'unita' operativa per
    il servito), che hanno data e ora esatte; per i giorni prima della
    prima transazione importata, dalle chiusure: cicli dei portali meno i
    lavaggi completi del servito."""
    from apps.finanze.models import ChiusuraCassaAutomatica, TransazionePortale
    from apps.ordini.models import ItemOrdine
    da_washtec = defaultdict(int)
    for orario in (TransazionePortale.objects.filter(origine='contanti', orario__date__gte=dal - timedelta(days=1),
                                                     orario__date__lte=al + timedelta(days=1))
                   .values_list('orario', flat=True)):
        g = timezone.localtime(orario).date()
        if dal <= g <= al:
            da_washtec[g] += 1
    prima_washtec = min(da_washtec) if da_washtec else None
    portali = {r['data']: r['c'] or 0 for r in
               ChiusuraCassaAutomatica.objects.filter(data__gte=dal, data__lte=al, cassa__tracking_washcycles=True,
                                                      cassa__modalita_registratore=False)
               .values('data').annotate(c=Sum('wash_cycles'))}
    servito = {r['ordine__data_ora__date']: r['q'] or 0 for r in
               ItemOrdine.objects.filter(ordine__data_ora__date__gte=dal, ordine__data_ora__date__lte=al,
                                         servizio_prodotto__titolo__icontains='lavaggio completo')
               .exclude(ordine__stato='annullato').values('ordine__data_ora__date').annotate(q=Sum('quantita'))}
    cicli = {g: (max(c - servito.get(g, 0), 0), 0.0) for g, c in portali.items()
             if c and (prima_washtec is None or g < prima_washtec)}
    cicli.update({g: (n, 0.0) for g, n in da_washtec.items()})
    return cicli


def _polvere(dal, al):
    """{data: polvere massima del giorno in microgrammi/m3} dalla qualita'
    dell'aria di Open-Meteo (modello CAMS): la sabbia del Sahara. {} se non
    risponde."""
    from apps.finanze.weather import LICATA_LAT, LICATA_LON
    base = {'latitude': LICATA_LAT, 'longitude': LICATA_LON, 'hourly': 'dust', 'timezone': 'Europe/Rome'}
    url = 'https://air-quality-api.open-meteo.com/v1/air-quality'
    dati = None
    for params in ({'start_date': dal.isoformat(), 'end_date': al.isoformat()}, {'past_days': 92, 'forecast_days': 7}):
        dati = _scarica(url, {**base, **params}, 60 * 60 * 3, modelli=False, chiave_dati='hourly')
        if dati:
            break
    if not dati:
        return {}
    massimi = defaultdict(float)
    for t, v in zip(dati['hourly'].get('time') or [], dati['hourly'].get('dust') or []):
        if v is not None:
            g = date.fromisoformat(t[:10])
            massimi[g] = max(massimi[g], float(v))
    return dict(massimi)


def _piste_per_giorno(dal, al):
    """Vendite del cambia gettoni (piste) per giorno, dalle chiusure."""
    from django.db.models import F

    from apps.finanze.models import ChiusuraCassaAutomatica
    righe = (ChiusuraCassaAutomatica.objects.filter(data__gte=dal, data__lte=al, cassa__tracking_washcycles=False,
                                                    cassa__modalita_registratore=False)
             .values('data').annotate(v=Sum(F('vendita_contante') + F('vendita_non_contante'))))
    return {r['data']: (float(r['v'] or 0), 0.0) for r in righe if r['v']}


FASCE = [('Mattina', 7, 13), ('Pomeriggio', 13, 18), ('Sera', 18, 23)]
GRUPPI_GIORNI = [('Lun-Ven', range(0, 5)), ('Sabato', (5,)), ('Domenica', (6,))]


def profilo_self(al, giorni=90):
    """Cicli self (pagati al portale dal cliente, non avviati dal servito)
    per ora del giorno, in media sui giorni con almeno un ciclo, per
    Lun-Ven / Sabato / Domenica. Le transazioni vengono da WashTec."""
    from apps.finanze.models import TransazionePortale
    dal = al - timedelta(days=giorni)
    conteggi = defaultdict(lambda: defaultdict(int))     # gruppo -> ora -> cicli
    giorni_gruppo = defaultdict(set)
    for orario in (TransazionePortale.objects.filter(origine='contanti', orario__date__gte=dal, orario__date__lte=al)
                   .values_list('orario', flat=True)):
        locale = timezone.localtime(orario)
        gruppo = next(n for n, wd in GRUPPI_GIORNI if locale.weekday() in wd)
        conteggi[gruppo][locale.hour] += 1
        giorni_gruppo[gruppo].add(locale.date())
    ore = range(7, 23)
    righe = []
    for gruppo, _ in GRUPPI_GIORNI:
        n = len(giorni_gruppo[gruppo])
        if not n:
            continue
        medie = [conteggi[gruppo][h] / n for h in ore]
        totale = sum(medie)
        fasce = [{'nome': nome, 'cicli': sum(conteggi[gruppo][h] for h in range(da, a)) / n,
                  'quota': (sum(conteggi[gruppo][h] for h in range(da, a)) / n / totale) if totale else 0}
                 for nome, da, a in FASCE]
        righe.append({'gruppo': gruppo, 'giorni': n, 'medie': medie, 'massimo': max(medie) if medie else 0,
                      'fasce': fasce, 'totale': totale})
    return {'ore': list(ore), 'righe': righe, 'dal': dal, 'al': al}


def _fasce_previste(giorno, cicli, profilo):
    """I cicli self previsti divisi nelle fasce con il profilo del tipo di
    giorno."""
    if cicli is None or not profilo:
        return None
    gruppo = next(n for n, wd in GRUPPI_GIORNI if giorno.weekday() in wd)
    riga = next((r for r in profilo['righe'] if r['gruppo'] == gruppo), None)
    if not riga or not riga['totale']:
        return None
    return [{'nome': f['nome'], 'cicli': cicli * f['quota']} for f in riga['fasce']]


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
                   'Pioggia oggi (mm)', 'Pioggia oggi (si/no)', 'Ore di pioggia oggi', 'Pioggia ieri', 'Pioggia 2-3 giorni fa',
                   'Giorni dall\'ultima pioggia', 'Temperatura massima', 'Livello ultime 4 settimane',
                   'Prenotazioni gia\' fatte'])
N_BASE = len(NOMI_VARIABILI)          # il modello di prima: solo la pioggia caduta
NOMI_VARIABILI += ['Pioggia prevista oggi (si/no)', 'Ore di pioggia previste oggi', 'Pioggia prevista domani (si/no)',
                   'Giorni di pioggia previsti nei 3 dopo', 'Pioggia prevista nei 3 giorni dopo (mm)',
                   'Probabilita\' di pioggia nei 3 giorni dopo']
N_PREVISTO = len(NOMI_VARIABILI)      # senza i segnali qui sotto
NOMI_VARIABILI += ['Polvere sahariana oggi', 'Polvere sahariana ieri',
                   'Self negli ultimi giorni (rispetto al solito)', 'Piste negli ultimi giorni (rispetto al solito)']


def _piove_previsto(m, con_probabilita):
    if not m:
        return False
    return piove(m) if con_probabilita else piove({**m, 'probabilita': None})


def _variabili(giorno, meteo, festivi, livello, prenotazioni, previsto=None, con_probabilita=False, segnali=None):
    wd = giorno.weekday()
    m = meteo.get(giorno, {})
    pioggia = lambda d: (meteo.get(giorno - timedelta(days=d)) or {}).get('pioggia', 0.0)
    asciutto = 0
    while asciutto < 14 and not piove(meteo.get(giorno - timedelta(days=asciutto + 1))):
        asciutto += 1
    angolo = 2 * math.pi * giorno.timetuple().tm_yday / 365.25
    tmax = m.get('tmax')
    return ([1.0 if wd == k else 0.0 for k in range(1, 7)] + [
        1.0 if giorno in festivi else 0.0,
        1.0 if giorno + timedelta(days=1) in festivi else 0.0,
        1.0 if giorno - timedelta(days=1) in festivi else 0.0,
        math.sin(angolo), math.cos(angolo),
        math.log1p(m.get('pioggia', 0.0)), 1.0 if piove(m) else 0.0, math.log1p(m.get('ore', 0.0)),
        math.log1p(pioggia(1)), math.log1p(pioggia(2) + pioggia(3)),
        asciutto / 14,
        tmax if tmax is not None else 22.0,
        livello,
        math.log1p(prenotazioni),
    ] + _variabili_previste(giorno, previsto or {}, con_probabilita) + _variabili_segnali(giorno, segnali or {}))


def _andamento(serie, giorno):
    """Log del rapporto fra la media dei 3 giorni noti alla sera prima
    (da 2 a 4 giorni prima) e quella delle 4 settimane prima: 0 = come al
    solito, positivo = piu' del solito. 0 se mancano i dati."""
    recenti = [serie[g][0] for g in (giorno - timedelta(days=k) for k in (2, 3, 4)) if g in serie]
    solito = [serie[g][0] for g in (giorno - timedelta(days=k) for k in range(2, 30)) if g in serie]
    if len(recenti) < 2 or len(solito) < 10:
        return 0.0
    media = sum(solito) / len(solito)
    if media <= 0:
        return 0.0
    return max(-1.0, min(1.0, math.log(max(sum(recenti) / len(recenti), 0.1) / media)))


def _variabili_segnali(giorno, segnali):
    """Polvere sahariana (oggi e ieri: dopo la sabbia si lava la macchina)
    e andamento recente di self e piste, termometro della voglia di lavare."""
    polvere = segnali.get('polvere') or {}
    return [
        math.log1p(polvere.get(giorno, 0.0)),
        math.log1p(polvere.get(giorno - timedelta(days=1), 0.0)),
        _andamento(segnali.get('self') or {}, giorno),
        _andamento(segnali.get('piste') or {}, giorno),
    ]


def _variabili_previste(giorno, previsto, con_probabilita):
    """La pioggia prevista per il giorno e per i 3 giorni dopo: chi vede
    pioggia in arrivo non lava la macchina, anche se poi piove poco."""
    oggi = previsto.get(giorno) or {}
    dopo = [previsto.get(giorno + timedelta(days=k)) or {} for k in (1, 2, 3)]
    probabilita = [m.get('probabilita') or 0 for m in dopo] if con_probabilita else [0]
    return [
        1.0 if _piove_previsto(oggi, con_probabilita) else 0.0,
        math.log1p(oggi.get('ore', 0.0)),
        1.0 if _piove_previsto(dopo[0], con_probabilita) else 0.0,
        sum(1 for m in dopo if _piove_previsto(m, con_probabilita)) / 3,
        math.log1p(sum(m.get('pioggia', 0.0) for m in dopo)),
        max(probabilita) / 100,
    ]


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
    def __init__(self, lam=10):
        self.lam = lam

    def fit(self, xs, ys):
        p = len(xs[0])
        self.media = [sum(x[j] for x in xs) / len(xs) for j in range(p)]
        self.scarto = [math.sqrt(sum((x[j] - self.media[j]) ** 2 for x in xs) / len(xs)) or 1.0 for j in range(p)]
        zs = [self._z(x) for x in xs]
        self.y0 = sum(ys) / len(ys)
        ata = [[sum(z[i] * z[j] for z in zs) + (self.lam if i == j else 0) for j in range(p)] for i in range(p)]
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

def _righe(giorni, storico, meteo, festivi, prenotazioni, previsto=None, con_probabilita=False, segnali=None):
    xs, ys, usati = [], [], []
    for g in giorni:
        livello = _livello(storico, g)
        if livello is None or g not in meteo:
            continue
        xs.append(_variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0), previsto, con_probabilita, segnali))
        ys.append(math.log1p(storico[g][0]) if g in storico else None)
        usati.append(g)
    return xs, ys, usati


def _ore_persona(storico, presenze):
    """Ore medie di una presenza nei giorni di apertura."""
    giorni = [g for g in presenze if g in storico]
    return (sum(presenze[g][0] for g in giorni) / sum(presenze[g][1] for g in giorni)) if giorni else None


def squadra_servito(lavaggi, lavaggi_per_persona):
    if not lavaggi_per_persona:
        return None
    return max(SQUADRA_MINIMA, math.ceil(lavaggi / lavaggi_per_persona - 1e-9))


def persone_self(cicli, soglia):
    """1 se i cicli self previsti arrivano alla soglia; senza soglia
    impostata (o senza previsione dei cicli) sempre 1."""
    if soglia is None or cicli is None:
        return 1
    return 1 if cicli >= soglia else 0


def squadra_per(lavaggi, cicli, par):
    servito = squadra_servito(lavaggi, par.lavaggi_per_persona_f)
    return servito + persone_self(cicli, par.soglia_cicli_self) if servito else None


def _taratura(xs, ys, persone=None):
    """Sceglie la regolarizzazione e il margine sugli ultimi giorni
    dell'addestramento, mai visti dal modello che li prevede: il margine
    e' il piu' piccolo che lascia scoperti (squadra minore di quella che
    sarebbe servita) al massimo OBIETTIVO_SCOPERTI dei giorni."""
    n = min(GIORNI_TARATURA, len(xs) // 4)
    dentro, fuori = slice(0, len(xs) - n), slice(len(xs) - n, len(xs))
    migliore = None
    for lam in LAMBDE:
        m = Ridge(lam).fit(xs[dentro], ys[dentro])
        errore = sum(abs(math.expm1(m.predict(x)) - math.expm1(y)) for x, y in zip(xs[fuori], ys[fuori]))
        if migliore is None or errore < migliore[0]:
            migliore = (errore, lam, m)
    errore_migliore, lam, m = migliore
    residui = [y - m.predict(x) for x, y in zip(xs[fuori], ys[fuori])]
    margine = _quantile(residui, 0.75)
    if persone:
        for q in [i / 20 for i in range(6, 20)]:                  # 30% ... 95%
            prova = _quantile(residui, q)
            scoperti = sum(1 for x, y in zip(xs[fuori], ys[fuori])
                           if persone(math.expm1(m.predict(x) + prova)) < persone(math.expm1(y)))
            if scoperti <= OBIETTIVO_SCOPERTI * len(residui):
                margine = prova
                break
    return lam, margine, errore_migliore / max(n, 1)


def _indice(nome):
    return NOMI_VARIABILI.index(nome)


def _effetti(modello, xs, previsto_piovoso):
    """Effetto sui lavaggi di situazioni concrete, a parita' del resto:
    per ogni giorno dello storico si confronta la previsione con e senza la
    situazione e si fa la media. Piu' leggibile dei pesi delle singole
    variabili, che si compensano fra loro (es. pioggia si/no e ore)."""
    def con(x, valori):
        y = list(x)
        for nome, v in valori.items():
            if _indice(nome) < len(y):
                y[_indice(nome)] = v
        return y

    def rapporto(acceso, spento):
        a = sum(math.exp(modello.predict(con(x, acceso))) for x in xs)
        b = sum(math.exp(modello.predict(con(x, spento))) for x in xs)
        return (a / b - 1) * 100 if b else 0

    giorni = ['Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
    settimana = lambda attivo: {g: 1.0 if g == attivo else 0.0 for g in giorni}
    feriali = [settimana(None)] + [settimana(g) for g in ('Mar', 'Mer', 'Gio', 'Ven')]

    def contro_feriali(attivo):
        a = sum(math.exp(modello.predict(con(x, settimana(attivo)))) for x in xs)
        b = sum(math.exp(modello.predict(con(x, f))) for x in xs for f in feriali) / len(feriali)
        return (a / b - 1) * 100

    p = previsto_piovoso       # valori tipici di un giorno di pioggia
    oggi_piove = {'Pioggia oggi (mm)': p['mm'], 'Pioggia oggi (si/no)': 1.0, 'Ore di pioggia oggi': p['ore'],
                  'Pioggia prevista oggi (si/no)': 1.0, 'Ore di pioggia previste oggi': p['ore']}
    oggi_asciutto = {k: 0.0 for k in oggi_piove}
    falso_allarme = {**oggi_asciutto, 'Pioggia prevista oggi (si/no)': 1.0, 'Ore di pioggia previste oggi': p['ore']}
    domani = {'Pioggia prevista domani (si/no)': 1.0, 'Giorni di pioggia previsti nei 3 dopo': 1 / 3,
              'Pioggia prevista nei 3 giorni dopo (mm)': p['mm']}
    domani_no = {k: 0.0 for k in domani}
    ieri = {'Pioggia ieri': p['mm']}
    estate = {'Stagione (sen)': math.sin(2 * math.pi * 196 / 365.25), 'Stagione (cos)': math.cos(2 * math.pi * 196 / 365.25)}
    inverno = {'Stagione (sen)': math.sin(2 * math.pi * 15 / 365.25), 'Stagione (cos)': math.cos(2 * math.pi * 15 / 365.25)}
    temp = lambda x: x[_indice('Temperatura massima')]
    effetti = [
        ('Giornata di pioggia (prevista e caduta)', rapporto(oggi_piove, oggi_asciutto)),
        ('Pioggia prevista ma non caduta', rapporto(falso_allarme, oggi_asciutto)),
        ('Pioggia prevista domani', rapporto(domani, domani_no)),
        ('Ha piovuto ieri', rapporto(ieri, {'Pioggia ieri': 0.0})),
        ('Sabato, rispetto a un feriale', contro_feriali('Sab')),
        ('Domenica, rispetto a un feriale', contro_feriali('Dom')),
        ('Festivo', rapporto({'Festivo': 1.0}, {'Festivo': 0.0})),
        ('Estate, rispetto all\'inverno', rapporto(estate, inverno)),
        ('5 gradi in piu\'', (sum(math.exp(modello.predict(con(x, {'Temperatura massima': temp(x) + 5}))) for x in xs)
                             / sum(math.exp(modello.predict(x)) for x in xs) - 1) * 100),
        ('3 prenotazioni in piu\'', rapporto({'Prenotazioni gia\' fatte': math.log1p(3)}, {'Prenotazioni gia\' fatte': 0.0})),
    ]
    if len(xs[0]) > N_PREVISTO:
        sabbia = math.log1p(100)                       # giornata di sabbia: ~100 microgrammi/m3
        effetti += [
            ('Sabbia del Sahara ieri', rapporto({'Polvere sahariana ieri': sabbia}, {'Polvere sahariana ieri': 0.0})),
            ('Sabbia del Sahara oggi', rapporto({'Polvere sahariana oggi': sabbia}, {'Polvere sahariana oggi': 0.0})),
            ('Self del 30% sopra il solito nei giorni prima',
             rapporto({'Self negli ultimi giorni (rispetto al solito)': math.log(1.3)},
                      {'Self negli ultimi giorni (rispetto al solito)': 0.0})),
            ('Piste del 30% sopra il solito nei giorni prima',
             rapporto({'Piste negli ultimi giorni (rispetto al solito)': math.log(1.3)},
                      {'Piste negli ultimi giorni (rispetto al solito)': 0.0})),
        ]
    return [{'nome': n, 'effetto': e} for n, e in effetti]


def _pioggia_tipica(previsto):
    piovosi = [m for m in previsto.values() if m.get('piove')]
    return {'mm': math.log1p(_quantile([m.get('pioggia', 0) for m in piovosi], 0.5)) if piovosi else math.log1p(5),
            'ore': math.log1p(_quantile([m.get('ore', 0) for m in piovosi], 0.5)) if piovosi else math.log1p(4)}


def _modello_cicli(xs, ys, usati, soglia, fino_a=None):
    """Modello dei cicli self addestrato sui giorni prima di fino_a, con il
    suo margine: come per il servito, il piu' piccolo che lascia il self
    scoperto (cicli oltre la soglia senza nessuno) al massimo 1 giorno su
    10. (None, 0) se i giorni sono pochi."""
    dentro = [i for i, g in enumerate(usati) if fino_a is None or g < fino_a]
    if len(dentro) < 60:
        return None, 0.0
    xs_d, ys_d = [xs[i] for i in dentro], [ys[i] for i in dentro]
    persone = (lambda c: persone_self(c, soglia)) if soglia is not None else None
    lam, margine, _ = _taratura(xs_d, ys_d, persone)
    return Ridge(lam).fit(xs_d, ys_d), (margine if soglia is not None else 0.0)


def calcola(oggi=None, par=None):
    """Verifica del modello sugli ultimi giorni e previsione dei prossimi 7."""
    from .models import ParametriPrevisione
    par = par or ParametriPrevisione.attuali()
    lpp = par.lavaggi_per_persona_f
    oggi = oggi or timezone.localdate()
    ieri = oggi - timedelta(days=1)
    fine = oggi + timedelta(days=10)          # 7 giorni di previsione + i 3 dopo per la pioggia in arrivo
    storico = {g: v for g, v in _lavaggi_per_giorno(INIZIO_ORDINI, ieri).items() if v[0] > 0}
    if len(storico) < 90:
        return {'errore': f'Servono almeno 90 giorni con ordini: ce ne sono {len(storico)}.'}
    meteo = _meteo(INIZIO_ORDINI - timedelta(days=3), fine)
    if meteo is None:
        return {'errore': 'Il servizio meteo (Open-Meteo) non risponde: riprova fra qualche minuto.'}
    storico_previsioni = _meteo_previsto_storico(INIZIO_ORDINI, ieri)
    # fino a ieri le previsioni di allora, da oggi quelle attuali
    previsto = {g: m for g, m in (storico_previsioni or meteo).items() if g < oggi}
    previsto.update({g: m for g, m in meteo.items() if g >= oggi})
    con_probabilita = bool(storico_previsioni) and (
        sum(1 for m in storico_previsioni.values() if m.get('probabilita') is not None) >= 0.8 * len(storico_previsioni))
    festivi = italian_holidays_in_range(INIZIO_ORDINI, fine + timedelta(days=1))
    prenotazioni = _prenotazioni_note(INIZIO_ORDINI, fine)
    presenze = _presenze_per_giorno(INIZIO_ORDINI, ieri)
    ore_persona = _ore_persona(storico, presenze)
    costo_ora = _costo_orario_per_mese()
    costo_medio = sum(costo_ora.values()) / len(costo_ora) if costo_ora else None
    cicli = _cicli_self_per_giorno(INIZIO_ORDINI, ieri)
    segnali = {'polvere': _polvere(INIZIO_ORDINI - timedelta(days=3), fine), 'self': cicli,
               'piste': _piste_per_giorno(INIZIO_ORDINI, ieri)}

    # giorni di chiusura: giorni della settimana quasi sempre senza ordini
    aperti_wd = defaultdict(int)
    for g in storico:
        aperti_wd[g.weekday()] += 1
    settimane = max(1, (ieri - INIZIO_ORDINI).days / 7)
    chiuso_wd = {wd for wd in range(7) if aperti_wd[wd] < settimane * 0.2}
    # festivi passati senza ordini: chiusi anche quest'anno
    festivi_chiusi = {nome for f, nome in festivi.items() if f <= ieri and f not in storico}

    xs, ys, usati = _righe(sorted(storico), storico, meteo, festivi, prenotazioni, previsto, con_probabilita, segnali)
    xs_c, ys_c, usati_c = _righe(sorted(cicli), cicli, meteo, festivi, {}, previsto, con_probabilita, segnali)
    # polvere e andamento del self restano solo se fanno sbagliare di meno
    # (sugli ultimi giorni dell'addestramento, mai visti dal modello)
    taglio_scelta = max(len(usati) - GIORNI_VERIFICA, 60)
    corto_p = lambda lista: [x[:N_PREVISTO] for x in lista]
    errore_con = _taratura(xs[:taglio_scelta], ys[:taglio_scelta])[2]
    errore_senza = _taratura(corto_p(xs[:taglio_scelta]), ys[:taglio_scelta])[2]
    usa_segnali = errore_con < errore_senza
    if not usa_segnali:
        xs, xs_c = corto_p(xs), corto_p(xs_c)
    n_variabili = len(xs[0])
    persone = (lambda n: squadra_servito(n, lpp)) if lpp else None

    # --- verifica: addestro senza gli ultimi giorni e li prevedo
    taglio = max(len(usati) - GIORNI_VERIFICA, 60)
    verifica = None
    if len(usati) - taglio >= 20:
        lam_v, margine_v, _ = _taratura(xs[:taglio], ys[:taglio], persone)
        modello_v = Ridge(lam_v).fit(xs[:taglio], ys[:taglio])
        dal_v = usati[taglio]
        modello_cv, margine_cv = _modello_cicli(xs_c, ys_c, usati_c, par.soglia_cicli_self, dal_v)
        cicli_v = ({g: (max(0.0, math.expm1(modello_cv.predict(x))), max(0.0, math.expm1(modello_cv.predict(x) + margine_cv)))
                    for x, g in zip(xs_c, usati_c) if g >= dal_v} if modello_cv else {})
        giorni_v = []
        for x, y, g in zip(xs[taglio:], ys[taglio:], usati[taglio:]):
            previsti = max(0.0, math.expm1(modello_v.predict(x)))
            reali = storico[g][0]
            riga = {'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'previsti': previsti, 'reali': reali,
                    'pioggia': meteo[g]['pioggia'], 'piove': meteo[g]['piove'],
                    'in_arrivo': _giorni_in_arrivo(g, previsto, con_probabilita), 'scarto_pct': (previsti - reali) / reali * 100,
                    'cicli_previsti': cicli_v[g][0] if g in cicli_v else None,
                    'cicli_reali': cicli[g][0] if g in cicli else None}
            cicli_alti = cicli_v[g][1] if g in cicli_v else None
            riga['servito'] = squadra_servito(math.expm1(modello_v.predict(x) + margine_v), lpp)
            riga['servito_ideale'] = squadra_servito(reali, lpp)
            riga['self'] = persone_self(cicli_alti, par.soglia_cicli_self)
            riga['self_ideale'] = persone_self(riga['cicli_reali'] if riga['cicli_reali'] is not None else cicli_alti,
                                               par.soglia_cicli_self)
            riga['squadra'] = riga['servito'] + riga['self'] if riga['servito'] else None
            riga['squadra_ideale'] = riga['servito_ideale'] + riga['self_ideale'] if riga['servito_ideale'] else None
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
        # lo stesso modello senza la pioggia prevista (com'era prima)
        corto = lambda x: x[:N_BASE]
        modello_prima = Ridge(lam_v).fit([corto(x) for x in xs[:taglio]], ys[:taglio])
        prima = [abs(max(0.0, math.expm1(modello_prima.predict(corto(x)))) - storico[g][0])
                 for x, g in zip(xs[taglio:], usati[taglio:])]
        modello_senza = Ridge(lam_v).fit(corto_p(xs[:taglio]), ys[:taglio])
        senza = [abs(max(0.0, math.expm1(modello_senza.predict(x[:N_PREVISTO]))) - storico[g][0])
                 for x, g in zip(xs[taglio:], usati[taglio:])]
        con_cicli = [r for r in giorni_v if r['cicli_previsti'] is not None and r['cicli_reali'] is not None]
        media_cicli = sum(r['cicli_reali'] for r in con_cicli) / len(con_cicli) if con_cicli else 0
        verifica = {
            'errore_prima_pct': sum(prima) / len(prima) / media_reali * 100,
            'errore_senza_segnali_pct': sum(senza) / len(senza) / media_reali * 100,
            'giorni': giorni_v, 'dal': giorni_v[0]['data'], 'al': giorni_v[-1]['data'],
            'errore_medio': sum(errori) / len(errori), 'errore_pct': sum(errori) / len(errori) / media_reali * 100,
            'errore_base_pct': sum(base) / len(base) / media_reali * 100,
            'entro_20': sum(1 for r in giorni_v if abs(r['scarto_pct']) <= 20) / len(giorni_v) * 100,
            'errore_cicli_pct': (sum(abs(r['cicli_previsti'] - r['cicli_reali']) for r in con_cicli) / len(con_cicli)
                                 / media_cicli * 100 if media_cicli else None),
            'squadra_giusta': (sum(1 for r in con_squadra if r['squadra'] == r['squadra_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'squadra_quasi': (sum(1 for r in con_squadra if 0 <= r['squadra'] - r['squadra_ideale'] <= 1) / len(con_squadra) * 100
                              if con_squadra else None),
            'squadra_scarsa': (sum(1 for r in con_squadra if r['squadra'] < r['squadra_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'servito_giusto': (sum(1 for r in con_squadra if r['servito'] == r['servito_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'servito_scarso': (sum(1 for r in con_squadra if r['servito'] < r['servito_ideale']) / len(con_squadra) * 100
                               if con_squadra else None),
            'self_giusto': (sum(1 for r in con_cicli if r['self'] == r['self_ideale']) / len(con_cicli) * 100
                            if con_cicli and par.soglia_cicli_self is not None else None),
            'self_scarso': (sum(1 for r in con_cicli if r['self'] < r['self_ideale']) / len(con_cicli) * 100
                            if con_cicli and par.soglia_cicli_self is not None else None),
            'giorni_presenze': len(con_presenze),
            'ore_risparmiate': sum(r.get('ore_risparmiate', 0) for r in con_presenze),
            'risparmio': sum(r.get('risparmio') or 0 for r in con_presenze),
        }

    # --- modelli finali su tutto lo storico
    lam, margine, _ = _taratura(xs, ys, persone)
    modello = Ridge(lam).fit(xs, ys)
    modello_c, margine_c = _modello_cicli(xs_c, ys_c, usati_c, par.soglia_cicli_self)
    previsioni = []
    profilo = profilo_self(ieri)
    storico_esteso, cicli_esteso = dict(storico), dict(cicli)
    finora = _lavaggi_per_giorno(oggi, oggi).get(oggi, (0, 0))[0]
    for k in range(0, 8):
        g = oggi + timedelta(days=k)
        if g not in meteo:
            continue
        chiuso = g.weekday() in chiuso_wd or festivi.get(g) in festivi_chiusi
        livello = _livello(storico_esteso, g) or _livello(storico, oggi)
        note = _prenotazioni_ora(g)
        # oggi: le prenotazioni note alle 18 di ieri, come nell'addestramento
        x = _variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0) if k == 0 else note, previsto, con_probabilita,
                       segnali)[:n_variabili]
        stima = modello.predict(x)
        previsti, alti = max(0.0, math.expm1(stima)), max(0.0, math.expm1(stima + margine))
        cicli_previsti = cicli_alti = None
        livello_c = _livello(cicli_esteso, g)
        if modello_c and livello_c is not None:
            stima_c = modello_c.predict(_variabili(g, meteo, festivi, livello_c, 0, previsto, con_probabilita,
                                                   segnali)[:n_variabili])
            cicli_previsti, cicli_alti = max(0.0, math.expm1(stima_c)), max(0.0, math.expm1(stima_c + margine_c))
        servito = None if chiuso else squadra_servito(alti, lpp)
        al_self = None if chiuso else persone_self(cicli_alti, par.soglia_cicli_self)
        previsioni.append({
            'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'chiuso': chiuso, 'domani': k == 1,
            'oggi': k == 0, 'finora': finora if k == 0 else None,
            'pioggia': meteo[g]['pioggia'], 'piove': meteo[g]['piove'], 'ore_pioggia': meteo[g].get('ore', 0),
            'probabilita': meteo[g].get('probabilita'), 'fonte': meteo[g].get('fonte'),
            'in_arrivo': _giorni_in_arrivo(g, previsto, con_probabilita), 'tmax': meteo[g]['tmax'], 'prenotazioni': note,
            'previsti': previsti, 'alti': alti, 'cicli': cicli_previsti, 'cicli_alti': cicli_alti,
            'fasce': _fasce_previste(g, cicli_previsti, profilo), 'polvere': segnali['polvere'].get(g),
            'servito': servito, 'self': al_self,
            'squadra': servito + al_self if servito else None,
        })
        if k > 0:
            storico_esteso[g] = (round(previsti), 0)
            if cicli_previsti is not None:
                cicli_esteso[g] = (round(cicli_previsti), 0)

    return {
        'previsioni_storiche': storico_previsioni is not None, 'con_probabilita': con_probabilita,
        'oggi': oggi, 'giorni_storico': len(usati), 'dal': usati[0], 'previsioni': previsioni, 'verifica': verifica,
        'par': par, 'ore_persona': ore_persona, 'giorni_cicli': len(usati_c), 'cicli_disponibili': modello_c is not None,
        'esempio_20': squadra_servito(20, lpp), 'giorni_presenze': len(presenze),
        'costo_ora': costo_medio,
        'effetti': [e for e in _effetti(modello, xs, _pioggia_tipica(previsto))
                    if not (e['nome'].startswith('Domenica') and 6 in chiuso_wd)],
        'obiettivo_scoperti': OBIETTIVO_SCOPERTI * 100, 'profilo': profilo,
        'usa_segnali': usa_segnali, 'polvere_disponibile': bool(segnali['polvere']),
        'piste_disponibili': bool(segnali['piste']),
        'margine_pct': (math.exp(margine) - 1) * 100, 'margine_cicli_pct': (math.exp(margine_c) - 1) * 100,
        'chiuso': [GIORNI_SETTIMANA[wd] for wd in sorted(chiuso_wd)],
    }


def _giorni_in_arrivo(giorno, previsto, con_probabilita):
    """Quanti dei 3 giorni dopo erano (o sono) previsti di pioggia."""
    return sum(1 for k in (1, 2, 3) if _piove_previsto(previsto.get(giorno + timedelta(days=k)), con_probabilita))


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
        'squadra': domani['squadra'] or 0, 'pioggia_mm': round(domani['pioggia'], 1), 'chiuso': domani['chiuso'],
        'cicli_self': round(domani['cicli']) if domani['cicli'] is not None else None,
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
