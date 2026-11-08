"""Clienti e abbonamenti (Analitica > Clienti e abbonamenti).

Abbonamenti, per configurazione e in totale:
- ricavo di competenza = prezzo / 1,22 x giorni di validita' nel periodo /
  giorni di validita' totali (attivazione -> scadenza); prezzo = totale
  dell'ordine di acquisto, o il prezzo della configurazione;
- accessi = accessi autorizzati nel periodo;
- costo per accesso = costo del servizio usato (capacita' del servito),
  oppure il costo pieno medio per lavaggio se il servizio non ha minuti
  standard;
- abbonati in perdita = abbonamenti con costo degli accessi > ricavo di
  competenza.

Clienti (solo lavaggi con cliente):
- copertura = lavaggi con cliente / lavaggi;
- nuovi = clienti col primo lavaggio di sempre nel periodo;
- frequenza = lavaggi / clienti attivi;
- tasso di ritorno = clienti attivi con almeno 2 lavaggi negli ultimi 12
  mesi (fino alla fine del periodo) / clienti attivi;
- giorni fra due lavaggi = mediana degli intervalli negli ultimi 12 mesi;
- ricavo medio per cliente = totale dei loro ordini di lavaggio / 1,22 /
  clienti attivi."""
import statistics
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Min
from django.utils import timezone

from .conto_economico import IVA, ZERO, fine_mese

FASCE_FREQUENZA = [(1, 1, '1 lavaggio'), (2, 3, '2-3 lavaggi'), (4, 6, '4-6 lavaggi'), (7, None, '7 o più')]


def _div(a, b):
    return a / b if b else None


def _giorni(dal, al):
    return max((al - dal).days + 1, 0)


def abbonamenti(dal, al, costi_servizi, costo_lavaggio):
    from apps.abbonamenti.models import AccessoAbbonamento, Abbonamento
    elenco = list(Abbonamento.objects.exclude(stato='annullato')
                  .filter(data_attivazione__lte=al, data_scadenza__gte=dal)
                  .select_related('configurazione', 'ordine_acquisto'))
    if not elenco:
        return None
    accessi = defaultdict(lambda: defaultdict(int))
    for abb, servizio in (AccessoAbbonamento.objects.filter(abbonamento__in=elenco, autorizzato=True,
                                                            data_ora__date__gte=dal, data_ora__date__lte=al)
                          .values_list('abbonamento_id', 'servizio_id')):
        accessi[abb][servizio] += 1
    gruppi = {}
    for a in elenco:
        ordine = a.ordine_acquisto
        prezzo = ordine.totale_finale if ordine and ordine.stato != 'annullato' else a.configurazione.prezzo
        totali = max(_giorni(a.data_attivazione, a.data_scadenza), 1)
        nel_periodo = _giorni(max(a.data_attivazione, dal), min(a.data_scadenza, al))
        ricavo = (prezzo or ZERO) / IVA * nel_periodo / totali
        n = sum(accessi[a.pk].values())
        costo = sum(((costi_servizi.get(s) or costo_lavaggio or ZERO) * k for s, k in accessi[a.pk].items()), ZERO)
        g = gruppi.setdefault(a.configurazione_id, {'nome': a.configurazione.titolo, 'prezzo': a.configurazione.prezzo,
                                                    'abbonamenti': 0, 'venduti': 0, 'ricavo': ZERO, 'accessi': 0,
                                                    'costo': ZERO, 'in_perdita': 0})
        g['abbonamenti'] += 1
        g['venduti'] += dal <= a.data_attivazione <= al
        g['ricavo'] += ricavo
        g['accessi'] += n
        g['costo'] += costo
        g['in_perdita'] += costo > ricavo
    righe = sorted(gruppi.values(), key=lambda g: -g['ricavo'])
    totale = {'nome': 'Totale', 'prezzo': None}
    for chiave in ('abbonamenti', 'venduti', 'ricavo', 'accessi', 'costo', 'in_perdita'):
        totale[chiave] = sum((g[chiave] for g in righe), type(righe[0][chiave])())
    for g in righe + [totale]:
        g['margine'] = g['ricavo'] - g['costo']
        g['ricavo_accesso'] = _div(g['ricavo'], Decimal(g['accessi']))
        g['costo_accesso'] = _div(g['costo'], Decimal(g['accessi']))
        g['accessi_abbonamento'] = _div(Decimal(g['accessi']), Decimal(g['abbonamenti']))
    return {'righe': righe, 'totale': totale}


def clienti(dal, al):
    from apps.finanze.services.ricavi import ordini_di_lavaggio
    from apps.ordini.models import Ordine
    tutti = Ordine.objects.exclude(stato='annullato')
    del_periodo = ordini_di_lavaggio(tutti.filter(data_ora__date__gte=dal, data_ora__date__lte=al))
    lavaggi = del_periodo.count()
    if not lavaggi:
        return None
    righe = list(Ordine.objects.filter(id__in=del_periodo.values('id'), cliente__isnull=False)
                 .values_list('cliente_id', 'totale_finale'))
    per_cliente = defaultdict(lambda: [0, ZERO])
    for cliente, totale in righe:
        per_cliente[cliente][0] += 1
        per_cliente[cliente][1] += totale or ZERO
    attivi = len(per_cliente)
    if not attivi:
        return {'lavaggi': lavaggi, 'con_cliente': 0, 'copertura': ZERO, 'attivi': 0}
    lavaggio_ids = ordini_di_lavaggio(tutti).values('id')
    primi = dict(Ordine.objects.filter(id__in=lavaggio_ids, cliente_id__in=list(per_cliente))
                 .values('cliente_id').annotate(primo=Min('data_ora')).values_list('cliente_id', 'primo'))
    nuovi = sum(1 for p in primi.values() if dal <= timezone.localtime(p).date() <= al)
    # ultimi 12 mesi fino alla fine del periodo: ritorno e intervalli
    inizio_anno = al - timedelta(days=365)
    date_cliente = defaultdict(list)
    for cliente, quando in (Ordine.objects.filter(id__in=lavaggio_ids, cliente_id__in=list(per_cliente),
                                                  data_ora__date__gt=inizio_anno, data_ora__date__lte=al)
                            .values_list('cliente_id', 'data_ora')):
        date_cliente[cliente].append(timezone.localtime(quando).date())
    tornati = sum(1 for d in date_cliente.values() if len(d) >= 2)
    intervalli = []
    for d in date_cliente.values():
        d.sort()
        intervalli += [(b - a).days for a, b in zip(d, d[1:]) if (b - a).days > 0]
    con_cliente = sum(n for n, _ in per_cliente.values())
    fasce = []
    for minimo, massimo, nome in FASCE_FREQUENZA:
        n = sum(1 for k, _ in per_cliente.values() if k >= minimo and (massimo is None or k <= massimo))
        fasce.append({'nome': nome, 'clienti': n, 'quota': Decimal(n) * 100 / attivi})
    return {
        'lavaggi': lavaggi, 'con_cliente': con_cliente, 'copertura': Decimal(con_cliente) * 100 / lavaggi,
        'attivi': attivi, 'nuovi': nuovi, 'frequenza': Decimal(con_cliente) / attivi,
        'ritorno': Decimal(tornati) * 100 / attivi,
        'intervallo': statistics.median(intervalli) if intervalli else None,
        'ricavo_cliente': sum((t for _, t in per_cliente.values()), ZERO) / IVA / attivi,
        'fasce': fasce,
    }


def clienti_e_abbonamenti(mesi, base):
    from .analitica import calcola_centri
    from .capacita import capacita
    from .models import ParametriAnalitica
    dal, al = mesi[0], min(fine_mese(mesi[-1]), timezone.localdate())
    if al < dal:
        return {'abbonamenti': None, 'clienti': None}
    c = calcola_centri(mesi, base)
    sv = c['centri']['servito']
    pieno = sv['costi'] + sv['quota_comuni']
    cap = capacita(mesi, c['dati']['ore']['servito'], pieno, sv['totali']['costi_variabili'],
                   ParametriAnalitica.attuali().margine_obiettivo)
    costo_lavaggio = _div(pieno, Decimal(c['dati']['ordini_lavaggio']))
    return {
        'abbonamenti': abbonamenti(dal, al, cap['costi_servizi'], costo_lavaggio),
        'clienti': clienti(dal, al),
        'costo_lavaggio': costo_lavaggio,
    }
