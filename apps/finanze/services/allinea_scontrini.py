"""Allineamento della chiusura portali agli scontrini delle casse.

Ogni cassa automatica dei portali stampa a fine giornata uno scontrino
con WashCycles e vendite dall'ultima chiusura (registrati nella
ChiusuraCassaAutomatica del giorno). Le due casse vengono chiuse in
momenti diversi e il loro orologio non coincide con quello WashTec,
quindi per ciascun portale si cerca la fine della giornata: l'inizio e'
la fine del giorno prima, la fine cade tra due lavaggi consecutivi e
si sceglie prima per saldo (contanti WashTec a listino = vendita dello
scontrino), poi per numero di lavaggi (= WashCycles), poi per vicinanza
all'orario attuale. La fine resta sempre nella giornata (dalle 10 a
mezzanotte, di solito la sera; a volte la cassa si chiude in mattinata):
quello che non si riesce a far tornare viene segnalato come scarto.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.finanze.models import (PREZZI_PROGRAMMA_PORTALE, ChiusuraCassaAutomatica,
                                 TransazionePortale)

# Portale WashTec -> parola nel nome della cassa automatica
CASSA_PORTALE = {'A': 'azzurro', 'B': 'blu'}
# La chiusura degli scontrini cade nella giornata (di solito la sera, a
# volte in tarda mattinata): la fine
# proposta resta tra quest'ora e mezzanotte
SERA_DALLE = time(10, 0)


def chiusure_casse_portali(data):
    """{'A': ChiusuraCassaAutomatica | None, 'B': ...} del giorno."""
    esito = {'A': None, 'B': None}
    for c in ChiusuraCassaAutomatica.objects.filter(data=data).select_related('cassa'):
        nome = c.cassa.nome.lower()
        for portale, parola in CASSA_PORTALE.items():
            if parola in nome:
                esito[portale] = c
    return esito


def washcycles_scontrini(data):
    """{'A': n, 'B': n} dalle chiusure casse automatiche del giorno
    (None se lo scontrino non e' registrato o senza WashCycles)."""
    return {p: (c.wash_cycles if c else None)
            for p, c in chiusure_casse_portali(data).items()}


def _sera(data):
    """Fascia in cui puo' cadere la chiusura della giornata."""
    return (timezone.make_aware(datetime.combine(data, SERA_DALLE)),
            timezone.make_aware(datetime.combine(data, time(23, 59, 59))))


def inizio_continuo(chiusura, portale):
    """Inizio della giornata del portale: la fine della chiusura del giorno
    prima, se salvata (niente buchi ne' sovrapposizioni), altrimenti
    quello della chiusura."""
    from apps.finanze.models import ChiusuraPortali

    prec = ChiusuraPortali.objects.filter(data=chiusura.data - timedelta(days=1)).first()
    return prec.finestra(portale)[1] if prec else chiusura.finestra(portale)[0]


def allinea(chiusura, casse):
    """Per ogni portale con scontrino (casse = chiusure_casse_portali):
    {'scontrino', 'vendita', 'da', 'da_attuale', 'fine', 'fine_attuale',
    'tra': (min, max), 'conteggio', 'scarto', 'contanti', 'scarto_contanti',
    'errore'}.

    L'inizio e' la fine del giorno prima. La fine, nella sera della
    giornata, e' scelta per priorita':
    1. contanti dei lavaggi WashTec (a listino) uguali alla vendita
       contante + non contante dello scontrino (il saldo);
    2. numero di lavaggi uguale ai WashCycles dello scontrino;
    3. orario piu' vicino a quello attuale.
    'scarto' e 'scarto_contanti' dicono quanto resta se non si azzera.
    """
    esito = {}
    sera_da, sera_a = _sera(chiusura.data)
    for portale, cassa in casse.items():
        if cassa is None:
            continue
        n = cassa.wash_cycles
        vendita = cassa.vendita_totale
        da_attuale, fine_attuale = chiusura.finestra(portale)
        da = inizio_continuo(chiusura, portale)
        lavaggi = list(TransazionePortale.objects
                       .filter(portale=portale, orario__gt=da, orario__lte=sera_a)
                       .order_by('orario', 'numero')
                       .values_list('orario', 'programma', 'origine'))
        dopo = (TransazionePortale.objects
                .filter(portale=portale, orario__gt=sera_a)
                .order_by('orario').values_list('orario', flat=True).first())
        voce = {'scontrino': n, 'vendita': vendita, 'da': da, 'da_attuale': da_attuale,
                'fine_attuale': fine_attuale, 'fine': None, 'tra': None,
                'conteggio': None, 'scarto': 0, 'contanti': None, 'scarto_contanti': 0,
                'errore': ''}
        esito[portale] = voce
        if dopo is None and n is not None and len(lavaggi) < n:
            voce['errore'] = (f'in archivio ci sono solo {len(lavaggi)} lavaggi dopo '
                              "l'inizio: importa le transazioni WashTec mancanti")
            continue
        # Ogni k = lavaggi contati ha un intervallo di fine possibile:
        # dal k-esimo lavaggio a un secondo prima del successivo, tagliato
        # sulla sera della giornata
        candidati = []
        contanti = Decimal('0.00')
        for k in range(len(lavaggi) + 1):
            if k and lavaggi[k - 1][2] == 'contanti':
                contanti += PREZZI_PROGRAMMA_PORTALE[lavaggi[k - 1][1]]
            minimo = max(lavaggi[k - 1][0] if k else da, sera_da)
            successivo = lavaggi[k][0] if k < len(lavaggi) else dopo
            massimo = min(successivo - timedelta(seconds=1), sera_a) if successivo else sera_a
            if massimo < minimo:
                continue
            fine = min(max(fine_attuale, minimo), massimo).replace(microsecond=0)
            candidati.append((abs(contanti - vendita),
                              abs(k - n) if n is not None else 0,
                              abs((fine - fine_attuale).total_seconds()),
                              k, contanti, fine, minimo, massimo))
        if not candidati:
            voce['errore'] = 'nessun orario possibile nella giornata'
            continue
        _, _, _, k, contanti, fine, minimo, massimo = min(candidati)
        voce.update({'fine': fine, 'tra': (minimo, massimo), 'conteggio': k,
                     'scarto': (k - n) if n is not None else 0,
                     'contanti': contanti, 'scarto_contanti': contanti - vendita})
    return esito


# Pesi dell'allineamento di un periodo: un euro di scarto conta piu' di
# qualsiasi numero di cicli; la distanza dall'orario di riferimento
# serve solo a scegliere tra soluzioni equivalenti.
PESO_EURO = 1000
PESO_SECONDO = Decimal('0.000001')


def _riferimento_fine(data, portale):
    """Fine attuale della giornata del portale (salvata o 19:30)."""
    from apps.finanze.models import ChiusuraPortali

    salvata = ChiusuraPortali.objects.filter(data=data).first()
    if salvata:
        return salvata.finestra(portale)[1]
    return timezone.make_aware(datetime.combine(data, time(19, 30)))


def _inizio_periodo(dal, portale):
    from apps.finanze.models import ChiusuraPortali

    prec = ChiusuraPortali.objects.filter(data=dal - timedelta(days=1)).first()
    if prec:
        return prec.finestra(portale)[1]
    salvata = ChiusuraPortali.objects.filter(data=dal).first()
    if salvata:
        return salvata.finestra(portale)[0]
    return timezone.make_aware(datetime.combine(dal - timedelta(days=1), time(19, 30)))


def allinea_periodo(dal, al):
    """Fine di ogni giornata dal..al, per portale, scelta insieme per
    tutto il periodo (programmazione dinamica sui tagli tra lavaggi
    consecutivi): minimizza la somma degli scarti dei saldi e, a parita',
    quella dei WashCycles. Ritorna (inizi, {data: {portale: voce}}) con
    voce = {'fine', 'conteggio', 'contanti', 'scontrino', 'vendita'}."""
    giorni = [dal + timedelta(days=i) for i in range((al - dal).days + 1)]
    casse = {g: chiusure_casse_portali(g) for g in giorni}
    inizi, esito = {}, {g: {} for g in giorni}
    for portale in ('A', 'B'):
        # Inizio del periodo: la fine del giorno prima se salvata,
        # altrimenti libero nella sera del giorno prima (si sceglie
        # insieme al resto, vicino all'inizio attuale)
        from apps.finanze.models import ChiusuraPortali
        rif_inizio = _inizio_periodo(dal, portale)
        inizio_fisso = ChiusuraPortali.objects.filter(data=dal - timedelta(days=1)).exists()
        base = rif_inizio if inizio_fisso else _sera(dal - timedelta(days=1))[0]
        _, fine_ultima = _sera(al)
        righe = list(TransazionePortale.objects
                     .filter(portale=portale, orario__gt=base, orario__lte=fine_ultima)
                     .order_by('orario', 'numero')
                     .values_list('orario', 'programma', 'origine'))
        dopo = (TransazionePortale.objects
                .filter(portale=portale, orario__gt=fine_ultima)
                .order_by('orario').values_list('orario', flat=True).first())
        orari = [r[0] for r in righe]
        contanti = [Decimal('0.00')]
        for _, programma, origine in righe:
            contanti.append(contanti[-1] + (PREZZI_PROGRAMMA_PORTALE[programma]
                                            if origine == 'contanti' else 0))

        def tagli(giorno, rif):
            """(k lavaggi da base, orario, distanza da rif) possibili nella
            sera del giorno."""
            sera_da, sera_a = _sera(giorno)
            lista = []
            for k in range(len(orari) + 1):
                minimo = max(orari[k - 1] if k else base, sera_da)
                successivo = orari[k] if k < len(orari) else dopo
                massimo = min(successivo - timedelta(seconds=1), sera_a) if successivo else sera_a
                if massimo >= minimo:
                    fine = min(max(rif, minimo), massimo).replace(microsecond=0)
                    lista.append((k, fine, abs((fine - rif).total_seconds())))
            return lista

        candidati = [tagli(g, _riferimento_fine(g, portale)) for g in giorni]

        def costo(g, k_prima, k, distanza):
            cassa = casse[g][portale]
            c = PESO_SECONDO * Decimal(distanza)
            if cassa is None:
                return c
            c += PESO_EURO * abs(contanti[k] - contanti[k_prima] - cassa.vendita_totale)
            if cassa.wash_cycles is not None:
                c += abs(k - k_prima - cassa.wash_cycles)
            return c

        # programmazione dinamica: migliore[k] = (costo, percorso); il
        # primo elemento del percorso e' l'inizio
        if inizio_fisso:
            migliore = {0: (Decimal(0), [(0, base)])}
        else:
            migliore = {k: (PESO_SECONDO * Decimal(d), [(k, fine)])
                        for k, fine, d in tagli(dal - timedelta(days=1), rif_inizio)}
        for i, g in enumerate(giorni):
            # Giorno senza scontrino seguito da uno con scontrino: la cassa
            # non e' stata chiusa, i suoi lavaggi finiscono nello scontrino
            # successivo (giornata vuota, fine = None)
            if casse[g][portale] is None and any(casse[x][portale] for x in giorni[i + 1:]):
                migliore = {kp: (c, percorso + [(kp, None)]) for kp, (c, percorso) in migliore.items()}
                continue
            nuovo = {}
            for k, fine, distanza in candidati[i]:
                scelte = [(c + costo(g, kp, k, distanza), percorso)
                          for kp, (c, percorso) in migliore.items() if kp <= k]
                if scelte:
                    c, percorso = min(scelte, key=lambda s: s[0])
                    nuovo[k] = (c, percorso + [(k, fine)])
            migliore = nuovo
        if not migliore:
            continue
        _, percorso = min(migliore.values(), key=lambda s: s[0])
        k_prima, inizi[portale] = percorso[0]
        for g, (k, fine) in zip(giorni, percorso[1:]):
            cassa = casse[g][portale]
            esito[g][portale] = {
                'fine': fine, 'conteggio': k - k_prima,
                'contanti': contanti[k] - contanti[k_prima],
                'scontrino': cassa.wash_cycles if cassa else None,
                'vendita': cassa.vendita_totale if cassa else None,
            }
            k_prima = k
    return inizi, esito


def salva_periodo(dal, al, operatore=None):
    """Applica allinea_periodo salvando le ChiusuraPortali dei giorni,
    ognuna attaccata alla fine della precedente. Ritorna l'esito."""
    from apps.finanze.models import ChiusuraPortali

    inizi, esito = allinea_periodo(dal, al)
    da = dict(inizi)
    for g in sorted(esito):
        voce = esito[g]
        if 'A' not in voce or 'B' not in voce:
            continue
        # giornata senza chiusura di cassa: finestra vuota, i lavaggi
        # vanno al giorno dopo
        fine_a = voce['A']['fine'] or da['A']
        fine_b = voce['B']['fine'] or da['B']
        ChiusuraPortali.objects.update_or_create(data=g, defaults={
            'periodo_da': da['A'], 'periodo_a': fine_a,
            'periodo_da_blu': da['B'] if da['B'] != da['A'] else None,
            'periodo_a_blu': fine_b if fine_b != fine_a else None,
            'operatore': operatore,
        })
        da = {'A': fine_a, 'B': fine_b}
    return esito
