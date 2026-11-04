"""Proposte della pagina Cespiti: tutto quello che sta sulla voce "Acquisto
impianti e attrezzature" e non e' ancora un cespite. Le fatture valgono
l'imponibile delle loro righe su quella voce; i pagamenti senza fattura
valgono l'importo pagato, IVA compresa se c'era. Piu' proposte possono
fare un solo cespite (per esempio acconti e fattura di saldo). Eliminato
il cespite, le sue proposte tornano."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from .models import Cespite, MovimentoFinanziario, RigaFatturaPassiva

VOCE = 'N.INVESTIMENTI'


def _righe():
    return RigaFatturaPassiva.objects.filter(voce__codice=VOCE, cespite__isnull=True, non_cespite=False)


def _movimenti():
    return MovimentoFinanziario.objects.filter(voce__codice=VOCE, importo__lt=0, scadenze__isnull=True,
                                               cespite__isnull=True, non_cespite=False)


def proposte():
    per_fattura = defaultdict(list)
    for r in _righe().select_related('fattura__fornitore'):
        per_fattura[r.fattura].append(r)
    elenco = [{'chiave': f'f{f.pk}', 'fattura': f, 'data': f.data, 'nome': str(f.fornitore), 'testo': righe[0].descrizione[:90],
               'valore': sum((r.imponibile for r in righe), Decimal('0'))}
              for f, righe in per_fattura.items()]
    elenco += [{'chiave': f'm{m.pk}', 'movimento': m, 'data': m.data, 'nome': str(m.conto), 'testo': m.descrizione[:90],
                'valore': -m.importo, 'iva_compresa': True}
               for m in _movimenti().select_related('conto')]
    return sorted(elenco, key=lambda p: p['data'], reverse=True)


def _scelte(chiavi):
    fatture = [c[1:] for c in chiavi if c[:1] == 'f' and c[1:].isdigit()]
    movimenti = [c[1:] for c in chiavi if c[:1] == 'm' and c[1:].isdigit()]
    return _righe().filter(fattura__in=fatture), _movimenti().filter(pk__in=movimenti)


def crea_cespite(chiavi, **dati):
    """Un cespite dalle proposte scelte; None se non ce n'e' nessuna."""
    righe, movimenti = _scelte(chiavi)
    if not righe.exists() and not movimenti.exists():
        return None
    with transaction.atomic():
        cespite = Cespite.objects.create(**dati)
        righe.update(cespite=cespite)
        MovimentoFinanziario.objects.filter(pk__in=list(movimenti.values_list('pk', flat=True))).update(cespite=cespite)
    return cespite


def scarta(chiavi):
    righe, movimenti = _scelte(chiavi)
    return righe.update(non_cespite=True) + MovimentoFinanziario.objects.filter(
        pk__in=list(movimenti.values_list('pk', flat=True))).update(non_cespite=True)
