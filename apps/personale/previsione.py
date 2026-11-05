"""Previsione del lavoro del servito e squadra consigliata per il giorno dopo.

Due passi:
1. un modello (regressione ridge sul logaritmo dei lavaggi, addestrata su
   tutto lo storico degli ordini) stima quanti lavaggi del servito ci
   saranno, da: giorno della settimana, festivi e ponti, stagione, pioggia
   prevista il giorno prima, pioggia dei giorni passati, temperatura,
   sabbia del Sahara, livello delle ultime 4 settimane e dell'ultima, e
   prenotazioni gia' fatte alle 19 del giorno prima;
2. persone al servito = lavaggi previsti + un margine (parametro, di
   partenza 10%) / lavaggi che una persona fa in una giornata (parametro,
   di partenza 7: 20 lavaggi = 3 persone);
3. al self c'e' sempre PERSONE_SELF persona: la previsione dei cicli self
   sbagliava troppo (circa 38%) e peggiorava la squadra, quindi non si fa.
   Il profilo orario del self resta come informazione (dati storici).
Parametri in ParametriPrevisione, modificabili dalla pagina.

Il modello si verifica da solo: addestrato senza gli ultimi GIORNI_VERIFICA
giorni di apertura, prevede quei giorni e confronta con il reale. Per
questo impara e si verifica con le previsioni emesse davvero il giorno
prima (Open-Meteo Previous Runs), mai con la pioggia poi caduta. Le prove
sui dati del 2026 dicono che il caso da solo vale circa il 17% di errore
(circa 0,8 x radice dei lavaggi): stipendi, rimbalzo dopo la pioggia,
pioggia nei giorni dopo e andamento delle piste non hanno aiutato.
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
ORA_DECISIONE = time(19, 0)   # fine del turno: la giornata e' completa
LAMBDA = 100                 # regolarizzazione ridge, provata sui dati reali
SQUADRA_MINIMA = 1           # persone minime del servito
PERSONE_SELF = 1             # al self sempre una persona
MIN_LAVAGGI_DOMENICA = 3     # sotto, la domenica conta come servito chiuso (ordini isolati)
# modelli meteo di Open-Meteo, in ordine di preferenza: ItaliaMeteo-ARPAE ICON 2I
# (2,2 km sull'Italia, ~3 giorni), poi la scelta automatica per i giorni che non copre
MODELLI_METEO = ('italia_meteo_arpae_icon_2i', 'best_match')
NOMI_MODELLI = {'italia_meteo_arpae_icon_2i': 'ItaliaMeteo ICON-2I', 'best_match': 'Open-Meteo'}
GIORNI_SETTIMANA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


# ---------------------------------------------------------------- dati

def piove(m):
    """Giornata di pioggia: almeno 1 mm, oppure 2 ore di pioggia. Anche
    una pioggia debole per tutto il pomeriggio conta, anche se i
    millimetri sono pochi (vedi debole)."""
    return bool(m) and (m.get('pioggia', 0) >= 1 or m.get('ore', 0) >= 2)


def debole(m):
    """Pioggia di poche ore o pochi millimetri: nelle pagine non si chiama
    giornata di pioggia, per un cliente e' quasi asciutto."""
    return piove(m) and m.get('pioggia', 0) < 1


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


def _meteo_giorno_prima(dal, al):
    """La pioggia prevista per ogni giorno dalla previsione emessa il giorno
    prima (Open-Meteo Previous Runs): quello che si sapeva davvero alle 19
    della sera prima. {data: {'pioggia', 'ore', 'piove'}}; None se non risponde."""
    from apps.finanze.weather import LICATA_LAT, LICATA_LON
    params = {'latitude': LICATA_LAT, 'longitude': LICATA_LON, 'hourly': 'precipitation_previous_day1',
              'timezone': 'Europe/Rome', 'start_date': dal.isoformat(), 'end_date': al.isoformat()}
    dati = _scarica('https://previous-runs-api.open-meteo.com/v1/forecast', params, 60 * 60 * 12, modelli=False,
                    chiave_dati='hourly')
    if not dati:
        return None
    meteo = {}
    for t, mm in zip(dati['hourly'].get('time') or [], dati['hourly'].get('precipitation_previous_day1') or []):
        if mm is not None:
            m = meteo.setdefault(date.fromisoformat(t[:10]), {'pioggia': 0.0, 'ore': 0.0})
            m['pioggia'] += mm
            m['ore'] += 1 if mm >= 0.1 else 0
    for m in meteo.values():
        m['piove'] = piove(m)
    return meteo


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
        m['piove'], m['debole'] = piove(m), debole(m)
        meteo[giorno] = m
    return meteo


def _lavaggi_per_giorno(dal, al):
    from apps.ordini.models import Ordine
    righe = (Ordine.objects.filter(data_ora__date__gte=dal, data_ora__date__lte=al, vendita_prodotti=False)
             .exclude(stato='annullato').values('data_ora__date').annotate(n=Count('id'), incasso=Sum('totale_finale')))
    return {r['data_ora__date']: (r['n'], float(r['incasso'] or 0)) for r in righe}


def _cicli_dettaglio(dal, al):
    """Per giorno: transazioni WashTec pagate in contanti al portale, quelle
    da unita' operativa (cassa automatica: clienti del self e lavaggi del
    servito), cicli dei portali dalle chiusure, lavaggi completi del servito."""
    from apps.finanze.models import ChiusuraCassaAutomatica, TransazionePortale
    from apps.ordini.models import ItemOrdine
    giorni = defaultdict(lambda: {'self_washtec': 0, 'servito_washtec': 0, 'chiusure': None, 'completi': 0})
    for orario, origine in (TransazionePortale.objects.filter(orario__date__gte=dal - timedelta(days=1),
                                                              orario__date__lte=al + timedelta(days=1))
                            .values_list('orario', 'origine')):
        g = timezone.localtime(orario).date()
        if dal <= g <= al:
            giorni[g]['self_washtec' if origine == 'contanti' else 'servito_washtec'] += 1
    for r in (ChiusuraCassaAutomatica.objects.filter(data__gte=dal, data__lte=al, cassa__tracking_washcycles=True,
                                                     cassa__modalita_registratore=False)
              .values('data').annotate(c=Sum('wash_cycles'))):
        giorni[r['data']]['chiusure'] = r['c'] or 0
    for r in (ItemOrdine.objects.filter(ordine__data_ora__date__gte=dal, ordine__data_ora__date__lte=al,
                                        servizio_prodotto__titolo__icontains='lavaggio completo')
              .exclude(ordine__stato='annullato').values('ordine__data_ora__date').annotate(q=Sum('quantita'))):
        giorni[r['ordine__data_ora__date']]['completi'] = r['q'] or 0
    return giorni


def _washtec_completo(d):
    """Il giorno e' stato importato tutto da WashTec: le transazioni sono
    almeno l'80% dei cicli della chiusura (se c'e')."""
    totale = d['self_washtec'] + d['servito_washtec']
    if not totale:
        return False
    return d['chiusure'] is None or not d['chiusure'] or totale >= 0.8 * d['chiusure']


def dati_giornalieri(oggi=None):
    """Una riga per giorno dall'inizio degli ordini a ieri, con tutto quello
    che usa la previsione: per l'esportazione e la calibrazione."""
    oggi = oggi or timezone.localdate()
    ieri = oggi - timedelta(days=1)
    lavaggi = _lavaggi_per_giorno(INIZIO_ORDINI, ieri)
    cicli = _cicli_dettaglio(INIZIO_ORDINI, ieri)
    presenze = _presenze_per_giorno(INIZIO_ORDINI, ieri)
    piste = _piste_per_giorno(INIZIO_ORDINI, ieri)
    prenotazioni = _prenotazioni_note(INIZIO_ORDINI, ieri)
    meteo = _meteo(INIZIO_ORDINI, ieri) or {}
    previsto = _meteo_previsto_storico(INIZIO_ORDINI, ieri) or {}
    giorno_prima = _meteo_giorno_prima(INIZIO_ORDINI, ieri) or {}
    polvere = _polvere(INIZIO_ORDINI, ieri)
    festivi = italian_holidays_in_range(INIZIO_ORDINI, oggi)
    righe, g = [], INIZIO_ORDINI
    while g <= ieri:
        m, p, c = meteo.get(g, {}), previsto.get(g, {}), cicli.get(g, {})
        righe.append({
            'data': g.isoformat(), 'giorno': GIORNI_SETTIMANA[g.weekday()], 'festivo': festivi.get(g, ''),
            'lavaggi_servito': lavaggi.get(g, (0, 0))[0], 'incasso_servito': round(lavaggi.get(g, (0, 0))[1], 2),
            'prenotazioni_note_sera': prenotazioni.get(g, 0),
            'cicli_self_washtec': c.get('self_washtec', 0), 'cicli_servito_washtec': c.get('servito_washtec', 0),
            'cicli_portali_chiusure': c.get('chiusure') if c.get('chiusure') is not None else '',
            'lavaggi_completi_servito': c.get('completi', 0),
            'washtec_completo': 1 if c and _washtec_completo(c) else 0,
        'cicli_self': '' if not c else (max(c['self_washtec'] + c['servito_washtec'] - c['completi'], 0) if _washtec_completo(c)
                                        else (max(c['chiusure'] - c['completi'], 0) if c['chiusure'] else '')),
            'piste_euro': round(piste.get(g, (0, 0))[0], 2),
            'persone_presenti': presenze.get(g, (0, 0))[1], 'ore_presenze': presenze.get(g, (0, 0))[0],
            'pioggia_mm': m.get('pioggia', ''), 'pioggia_ore': m.get('ore', ''), 'piove': int(bool(m.get('piove'))) if m else '',
            'prevista_mm': p.get('pioggia', ''), 'prevista_ore': p.get('ore', ''),
            'prevista_probabilita': p.get('probabilita') if p.get('probabilita') is not None else '',
            'prevista_piove': int(bool(p.get('piove'))) if p else '', 'tmax': m.get('tmax', ''),
            'giorno_prima_mm': round(giorno_prima[g]['pioggia'], 1) if g in giorno_prima else '',
            'giorno_prima_ore': giorno_prima[g]['ore'] if g in giorno_prima else '',
            'polvere_max': round(polvere[g], 1) if g in polvere else '',
        })
        g += timedelta(days=1)
    return righe


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
    """Cicli dei portali per ora del giorno, in media sui giorni con almeno
    un ciclo, per Lun-Ven / Sabato / Domenica, dalle transazioni WashTec
    esclusi i lavaggi abbinati al servito (quelli non abbinati restano: la
    domenica, col servito chiuso, e' solo self)."""
    from apps.finanze.models import TransazionePortale
    dal = al - timedelta(days=giorni)
    conteggi = defaultdict(lambda: defaultdict(int))     # gruppo -> ora -> cicli
    giorni_gruppo = defaultdict(set)
    for orario in (TransazionePortale.objects.filter(orario__date__gte=dal, orario__date__lte=al, abbinamento__isnull=True)
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

NOMI_VARIABILI = ['Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom', 'Festivo', 'Prefestivo', 'Postfestivo',
                  'Stagione (sen)', 'Stagione (cos)', 'Pioggia ieri', 'Pioggia 2-3 giorni fa', 'Temperatura massima',
                  'Livello ultime 4 settimane', 'Livello ultima settimana', "Prenotazioni gia' fatte",
                  'Pioggia prevista (si/no)', 'Ore di pioggia previste', 'Pioggia prevista (mm)',
                  'Polvere sahariana oggi', 'Polvere sahariana ieri']


def _variabili(giorno, meteo, festivi, livello, prenotazioni, previsto, polvere, livello_breve=None):
    """Solo quello che si sa la sera prima: per il giorno stesso la pioggia
    prevista, mai quella caduta."""
    wd = giorno.weekday()
    pioggia = lambda d: (meteo.get(giorno - timedelta(days=d)) or {}).get('pioggia', 0.0)
    angolo = 2 * math.pi * giorno.timetuple().tm_yday / 365.25
    tmax = (meteo.get(giorno) or {}).get('tmax')
    attesa = previsto.get(giorno) or {}
    return [1.0 if wd == k else 0.0 for k in range(1, 7)] + [
        1.0 if giorno in festivi else 0.0,
        1.0 if giorno + timedelta(days=1) in festivi else 0.0,
        1.0 if giorno - timedelta(days=1) in festivi else 0.0,
        math.sin(angolo), math.cos(angolo),
        math.log1p(pioggia(1)), math.log1p(pioggia(2) + pioggia(3)),
        tmax if tmax is not None else 22.0,
        livello,
        livello_breve if livello_breve is not None else livello,
        math.log1p(prenotazioni),
        1.0 if piove(attesa) else 0.0,
        math.log1p(attesa.get('ore', 0.0)),
        math.log1p(attesa.get('pioggia', 0.0)),
        math.log1p(polvere.get(giorno, 0.0)),
        math.log1p(polvere.get(giorno - timedelta(days=1), 0.0)),
    ]


def _livello(storico, giorno, giorni=28):
    """Media del log dei lavaggi nei giorni di apertura dei `giorni` prima
    di `giorno` (escluso)."""
    valori = [math.log1p(storico[g][0]) for g in (giorno - timedelta(days=k) for k in range(1, giorni + 1)) if g in storico]
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
    def __init__(self, lam=LAMBDA):
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

def _righe(giorni, storico, meteo, festivi, prenotazioni, previsto, polvere):
    xs, ys, usati = [], [], []
    for g in giorni:
        livello = _livello(storico, g)
        if livello is None or g not in meteo:
            continue
        xs.append(_variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0), previsto, polvere, _livello(storico, g, 7)))
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


def squadra_per(lavaggi, par):
    """Squadra totale: servito + sempre una persona al self."""
    servito = squadra_servito(lavaggi, par.lavaggi_per_persona_f)
    return servito + PERSONE_SELF if servito else None


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
    pioggia = {'Pioggia prevista (si/no)': 1.0, 'Ore di pioggia previste': p['ore'], 'Pioggia prevista (mm)': p['mm']}
    ieri = {'Pioggia ieri': p['mm']}
    estate = {'Stagione (sen)': math.sin(2 * math.pi * 196 / 365.25), 'Stagione (cos)': math.cos(2 * math.pi * 196 / 365.25)}
    inverno = {'Stagione (sen)': math.sin(2 * math.pi * 15 / 365.25), 'Stagione (cos)': math.cos(2 * math.pi * 15 / 365.25)}
    temp = lambda x: x[_indice('Temperatura massima')]
    sabbia = math.log1p(100)                           # giornata di sabbia: ~100 microgrammi/m3
    effetti = [
        ('Giornata di pioggia prevista', rapporto(pioggia, {k: 0.0 for k in pioggia})),
        ('Ha piovuto ieri', rapporto(ieri, {'Pioggia ieri': 0.0})),
        ('Sabato, rispetto a un feriale', contro_feriali('Sab')),
        ('Domenica, rispetto a un feriale', contro_feriali('Dom')),
        ('Festivo', rapporto({'Festivo': 1.0}, {'Festivo': 0.0})),
        ("Estate, rispetto all'inverno", rapporto(estate, inverno)),
        ("5 gradi in piu'", (sum(math.exp(modello.predict(con(x, {'Temperatura massima': temp(x) + 5}))) for x in xs)
                             / sum(math.exp(modello.predict(x)) for x in xs) - 1) * 100),
        ("3 prenotazioni in piu'", rapporto({"Prenotazioni gia' fatte": math.log1p(3)}, {"Prenotazioni gia' fatte": 0.0})),
        ('Sabbia del Sahara ieri', rapporto({'Polvere sahariana ieri': sabbia}, {'Polvere sahariana ieri': 0.0})),
        ('Sabbia del Sahara oggi', rapporto({'Polvere sahariana oggi': sabbia}, {'Polvere sahariana oggi': 0.0})),
    ]
    return [{'nome': n, 'effetto': e} for n, e in effetti]


def _pioggia_tipica(previsto):
    piovosi = [m for m in previsto.values() if m.get('piove')]
    return {'mm': math.log1p(_quantile([m.get('pioggia', 0) for m in piovosi], 0.5)) if piovosi else math.log1p(5),
            'ore': math.log1p(_quantile([m.get('ore', 0) for m in piovosi], 0.5)) if piovosi else math.log1p(4)}


def calcola(oggi=None, par=None):
    """Verifica del modello sugli ultimi giorni e previsione dei prossimi 7."""
    from .models import ParametriPrevisione
    par = par or ParametriPrevisione.attuali()
    lpp = par.lavaggi_per_persona_f
    margine = par.margine_f
    oggi = oggi or timezone.localdate()
    ieri = oggi - timedelta(days=1)
    fine = oggi + timedelta(days=8)
    # le domeniche con 1-2 ordini isolati non sono giornate di servito aperto
    storico = {g: v for g, v in _lavaggi_per_giorno(INIZIO_ORDINI, ieri).items()
               if v[0] >= (MIN_LAVAGGI_DOMENICA if g.weekday() == 6 else 1)}
    if len(storico) < 90:
        return {'errore': f'Servono almeno 90 giorni con ordini: ce ne sono {len(storico)}.'}
    meteo = _meteo(INIZIO_ORDINI - timedelta(days=3), fine)
    if meteo is None:
        return {'errore': 'Il servizio meteo (Open-Meteo) non risponde: riprova fra qualche minuto.'}
    # fino a ieri la pioggia prevista il giorno prima, da oggi quella attuale
    giorno_prima = _meteo_giorno_prima(INIZIO_ORDINI, ieri)
    previsto = {g: m for g, m in (giorno_prima or meteo).items() if g < oggi}
    previsto.update({g: m for g, m in meteo.items() if g >= oggi})
    festivi = italian_holidays_in_range(INIZIO_ORDINI, fine + timedelta(days=1))
    prenotazioni = _prenotazioni_note(INIZIO_ORDINI, fine)
    presenze = _presenze_per_giorno(INIZIO_ORDINI, ieri)
    ore_persona = _ore_persona(storico, presenze)
    costo_ora = _costo_orario_per_mese()
    costo_medio = sum(costo_ora.values()) / len(costo_ora) if costo_ora else None
    polvere = _polvere(INIZIO_ORDINI - timedelta(days=3), fine)

    # giorni di chiusura: giorni della settimana quasi sempre senza ordini
    aperti_wd = defaultdict(int)
    for g in storico:
        aperti_wd[g.weekday()] += 1
    settimane = max(1, (ieri - INIZIO_ORDINI).days / 7)
    chiuso_wd = {wd for wd in range(7) if aperti_wd[wd] < settimane * 0.2}
    # festivi passati senza ordini: chiusi anche quest'anno
    festivi_chiusi = {nome for f, nome in festivi.items() if f <= ieri and f not in storico}
    # la domenica il servito apre solo in certi periodi (d'inverno si', d'estate no):
    # aperto se almeno 2 delle ultime 4 domeniche hanno avuto lavaggi veri
    ultime_domeniche = [ieri - timedelta(days=(ieri.weekday() - 6) % 7 + 7 * k) for k in range(4)]
    domenica_aperta = sum(1 for d in ultime_domeniche if d in storico) >= 2

    xs, ys, usati = _righe(sorted(storico), storico, meteo, festivi, prenotazioni, previsto, polvere)

    # --- verifica: addestro senza gli ultimi giorni e li prevedo
    taglio = max(len(usati) - GIORNI_VERIFICA, 60)
    verifica = None
    if len(usati) - taglio >= 20:
        modello_v = Ridge().fit(xs[:taglio], ys[:taglio])
        giorni_v = []
        for x, y, g in zip(xs[taglio:], ys[taglio:], usati[taglio:]):
            previsti = max(0.0, math.expm1(modello_v.predict(x)))
            reali = storico[g][0]
            riga = {'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'previsti': previsti, 'reali': reali,
                    'pioggia': meteo[g]['pioggia'], 'piove': meteo[g]['piove'],
                    'attesa': (previsto.get(g) or {}).get('pioggia', 0.0), 'scarto_pct': (previsti - reali) / reali * 100}
            riga['servito'] = squadra_servito(previsti * (1 + margine), lpp)
            riga['servito_ideale'] = squadra_servito(reali, lpp)
            riga['squadra'] = riga['servito'] + PERSONE_SELF if riga['servito'] else None
            riga['squadra_ideale'] = riga['servito_ideale'] + PERSONE_SELF if riga['servito_ideale'] else None
            if g in presenze:
                riga['persone'], riga['ore'] = presenze[g][1], presenze[g][0]
                # le vostre scelte: al servito le persone presenti meno una al self
                riga['servito_voi'] = max(riga['persone'] - 1, 0)
                # secondo parere: se il modello ne indica almeno 2 in meno, una in meno
                riga['servito_regola'] = (riga['servito_voi'] - 1 if riga['servito'] is not None
                                          and riga['servito'] <= riga['servito_voi'] - 2 else riga['servito_voi'])
            giorni_v.append(riga)
        errori = [abs(r['previsti'] - r['reali']) for r in giorni_v]
        con_squadra = [r for r in giorni_v if r['squadra'] and r['squadra_ideale']]
        con_presenze = [r for r in giorni_v if 'persone' in r]
        media_reali = sum(r['reali'] for r in giorni_v) / len(giorni_v)
        base = [abs(r['reali'] - _media_wd(storico, r['data'], usati[:taglio])) for r in giorni_v]
        # lo stesso modello senza la pioggia prevista
        senza_pioggia = [i for i in range(len(xs[0])) if not NOMI_VARIABILI[i].startswith(('Pioggia prevista', 'Ore di pioggia'))]
        corto = lambda x: [x[i] for i in senza_pioggia]
        modello_senza = Ridge().fit([corto(x) for x in xs[:taglio]], ys[:taglio])
        senza = [abs(max(0.0, math.expm1(modello_senza.predict(corto(x)))) - storico[g][0])
                 for x, g in zip(xs[taglio:], usati[taglio:])]
        verifica = {
            'errore_senza_pioggia_pct': sum(senza) / len(senza) / media_reali * 100,
            'limite_pct': sum(0.8 * math.sqrt(max(r['reali'], 1)) for r in giorni_v) / sum(r['reali'] for r in giorni_v) * 100,
            'giorni': giorni_v, 'dal': giorni_v[0]['data'], 'al': giorni_v[-1]['data'],
            'errore_medio': sum(errori) / len(errori), 'errore_pct': sum(errori) / len(errori) / media_reali * 100,
            'errore_base_pct': sum(base) / len(base) / media_reali * 100,
            'entro_20': sum(1 for r in giorni_v if abs(r['scarto_pct']) <= 20) / len(giorni_v) * 100,
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
            'giorni_presenze': len(con_presenze),
            'confronto': _confronto(con_presenze, ore_persona, costo_medio),
        }

    # --- modelli finali su tutto lo storico
    modello = Ridge().fit(xs, ys)
    previsioni = []
    profilo = profilo_self(ieri)
    storico_esteso = dict(storico)
    finora = _lavaggi_per_giorno(oggi, oggi).get(oggi, (0, 0))[0]
    # finito il turno oggi e' un giorno completo: il modello impara sapendo
    # i lavaggi del giorno prima, quindi domani deve vederli
    if turno_finito(oggi) and finora >= (MIN_LAVAGGI_DOMENICA if oggi.weekday() == 6 else 1):
        storico_esteso[oggi] = (finora, 0)
    for k in range(0, 8):
        g = oggi + timedelta(days=k)
        if g not in meteo:
            continue
        chiuso = (g.weekday() in chiuso_wd or festivi.get(g) in festivi_chiusi
                  or (g.weekday() == 6 and not domenica_aperta))           # chiuso il servito: il self resta
        livello = _livello(storico_esteso, g) or _livello(storico, oggi)
        note = _prenotazioni_ora(g)
        # oggi: le prenotazioni note ieri sera, come nell'addestramento
        x = _variabili(g, meteo, festivi, livello, prenotazioni.get(g, 0) if k == 0 else note, previsto, polvere,
                       _livello(storico_esteso, g, 7))
        previsti = max(0.0, math.expm1(modello.predict(x)))
        alti = previsti * (1 + margine)
        servito = 0 if chiuso else squadra_servito(alti, lpp)
        al_self = PERSONE_SELF
        previsioni.append({
            'data': g, 'giorno': GIORNI_SETTIMANA[g.weekday()], 'chiuso': chiuso, 'domani': k == 1,
            'oggi': k == 0, 'finora': finora if k == 0 else None,
            'pioggia': meteo[g]['pioggia'], 'piove': meteo[g]['piove'], 'debole': meteo[g].get('debole'),
            'ore_pioggia': meteo[g].get('ore', 0), 'probabilita': meteo[g].get('probabilita'), 'fonte': meteo[g].get('fonte'),
            'tmax': meteo[g]['tmax'], 'prenotazioni': note, 'previsti': previsti, 'alti': alti, 'polvere': polvere.get(g),
            'servito': servito, 'self': al_self,
            'squadra': servito + al_self if servito is not None else None,
            'togli_da': servito + 2 if servito else None,      # secondo parere: da quante al servito toglierne una
        })
        if k > 0:
            storico_esteso[g] = (round(previsti), 0)

    return {
        'previsioni_giorno_prima': giorno_prima is not None,
        'oggi': oggi, 'giorni_storico': len(usati), 'dal': usati[0], 'previsioni': previsioni, 'verifica': verifica,
        'par': par, 'ore_persona': ore_persona, 'persone_self': PERSONE_SELF,
        'esempio_20': squadra_servito(20, lpp), 'giorni_presenze': len(presenze),
        'costo_ora': costo_medio,
        'effetti': [e for e in _effetti(modello, xs, _pioggia_tipica(previsto))
                    if not (e['nome'].startswith('Domenica') and 6 in chiuso_wd)],
        'profilo': profilo, 'polvere_disponibile': bool(polvere), 'margine_pct': margine * 100,
        'chiuso': [GIORNI_SETTIMANA[wd] for wd in sorted(chiuso_wd)],
    }


def _confronto(giorni, ore_persona, costo_ora):
    """Servito: il modello, le vostre scelte (presenti meno una al self) e
    la regola del secondo parere (le vostre scelte, una in meno quando il
    modello ne indica almeno 2 in meno), sugli stessi giorni, contro la
    squadra che sarebbe servita con i lavaggi reali."""
    giorni = [r for r in giorni if r['servito'] is not None and r['servito_ideale']]
    if not giorni:
        return None

    def misura(chiave):
        d = [r[chiave] - r['servito_ideale'] for r in giorni]
        return {'giusta': sum(1 for x in d if x == 0) / len(d) * 100,
                'in_piu': sum(1 for x in d if x > 0) / len(d) * 100,
                'scoperti': sum(1 for x in d if x < 0) / len(d) * 100,
                'persone': sum(r[chiave] for r in giorni)}
    voi, modello, regola = misura('servito_voi'), misura('servito'), misura('servito_regola')
    risparmiate = voi['persone'] - regola['persone']
    return {'giorni': len(giorni), 'voi': voi, 'modello': modello, 'regola': regola,
            'ideale': sum(r['servito_ideale'] for r in giorni), 'risparmiate': risparmiate,
            'euro': risparmiate * ore_persona * costo_ora if ore_persona and costo_ora else None}


def _prenotazioni_ora(giorno):
    from apps.prenotazioni.models import Prenotazione
    return Prenotazione.objects.filter(slot__data=giorno).exclude(stato='annullata').count()


def _media_wd(storico, giorno, giorni):
    """Previsione "ingenua" di confronto: media dei lavaggi dello stesso
    giorno della settimana nello storico di addestramento."""
    valori = [storico[g][0] for g in giorni if g.weekday() == giorno.weekday()]
    return sum(valori) / len(valori) if valori else 0


def turno_finito(oggi=None):
    """True da quando il turno di `oggi` e' finito."""
    adesso = timezone.localtime()
    return adesso.time() >= ORA_DECISIONE and (oggi is None or adesso.date() == oggi)


def registra(risultato):
    """Salva la previsione di domani, a turno finito, per confrontarla poi
    con quanto e' successo. La prima salvata resta: chi riapre la pagina
    piu' tardi non deve riscriverla con un meteo piu' fresco."""
    from .models import PrevisioneSquadra
    domani = next((p for p in risultato.get('previsioni', []) if p['domani']), None)
    if not domani or not turno_finito():
        return None
    return PrevisioneSquadra.objects.get_or_create(data=domani['data'], defaults={
        'lavaggi_previsti': round(domani['previsti'], 1), 'lavaggi_alti': round(domani['alti'], 1),
        'squadra': domani['squadra'] or 0, 'pioggia_mm': round(domani['pioggia'], 1), 'chiuso': domani['chiuso'],
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


def errore_vero(storico):
    """Errore delle previsioni salvate davvero la sera prima, sui giorni
    con il servito aperto: {'giorni', 'errore_pct'} o None."""
    aperti = [s for s in storico if not s['p'].chiuso and s['reali']]
    if not aperti:
        return None
    errori = sum(abs(float(s['p'].lavaggi_previsti) - s['reali']) for s in aperti)
    return {'giorni': len(aperti), 'errore_pct': errori / sum(s['reali'] for s in aperti) * 100}
