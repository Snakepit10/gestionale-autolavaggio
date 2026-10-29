"""Correzione una tantum (confermata dal titolare il 14/10/2026).

Dal 12/10 al 14/10 lo scarico dei prodotti venduti falliva su PostgreSQL
(fix in services._ripartisci_uscita): alcune vendite sono rimaste senza
movimento di magazzino e l'ordine 20261014-0010 con il totale vecchio.
Qui si registrano le vendite mancanti (con la data dell'ordine) e si
ricalcola il totale di quell'ordine. Idempotente: ricontrolla quanto e'
gia' stato scaricato per ogni ordine e prodotto. Su un database senza
quegli ordini (test, sviluppo) non fa nulla.
"""
from decimal import Decimal

from django.db import migrations

ORDINI = ['20261012-0021', 'V20261013-001', 'V20261013-002', '20261014-0010']
DA_RICALCOLARE = ['20261014-0010']


def correggi(apps, schema_editor):
    # Senza quegli ordini non c'e' nulla da fare: controllato col modello
    # storico, perche' quello attuale puo' avere colonne non ancora create
    # (i test la chiamano con apps=None)
    if apps is not None and not apps.get_model('ordini', 'Ordine').objects.filter(numero_progressivo__in=ORDINI).exists():
        return
    # Modelli e servizi attuali: e' l'ultima migrazione, lo schema coincide
    from django.db.models import Sum

    from apps.magazzino.models import Articolo, Movimento
    from apps.magazzino.services import movimenta
    from apps.ordini.models import Ordine

    for ordine in Ordine.objects.filter(numero_progressivo__in=ORDINI).prefetch_related('items__servizio_prodotto'):
        venduti = {}
        for item in ordine.items.all():
            if item.servizio_prodotto.tipo == 'prodotto':
                venduti[item.servizio_prodotto_id] = venduti.get(item.servizio_prodotto_id, 0) + item.quantita
        for prodotto_id, quantita in venduti.items():
            articolo = Articolo.objects.filter(prodotto_id=prodotto_id).first()
            if articolo is None:
                continue
            gia = -(Movimento.objects.filter(articolo=articolo, ordine=ordine, tipo='vendita')
                    .aggregate(t=Sum('quantita'))['t'] or 0)
            mancano = quantita - gia
            if mancano <= 0:
                continue
            ultimo = Movimento.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
            movimenta(articolo, -mancano, 'vendita', operatore=ordine.operatore, ordine=ordine,
                      nota=f'Vendita - Ordine {ordine.numero_progressivo} (registrata il 14/10, '
                           'scarico non riuscito)',
                      primo=articolo.posto_vendita or articolo.posto)
            # la data e' quella dell'ordine (uno o piu' movimenti, uno per posto)
            Movimento.objects.filter(pk__gt=ultimo, articolo=articolo, ordine=ordine).update(data=ordine.data_ora)

        if ordine.numero_progressivo in DA_RICALCOLARE:
            ordine.totale = sum((i.subtotale for i in ordine.items.all()), Decimal('0'))
            ordine.importo_sconto = (ordine.sconto_applicato.calcola_sconto(ordine.totale)
                                     if ordine.sconto_applicato else Decimal('0'))
            ordine.totale_finale = ordine.totale - ordine.importo_sconto
            ordine.save(update_fields=['totale', 'importo_sconto', 'totale_finale'])
            ordine.aggiorna_stato_pagamento()


class Migration(migrations.Migration):

    dependencies = [
        ('magazzino', '0010_assegnazioni_ai_posti'),
        ('ordini', '0016_ordine_vendita_prodotti'),
    ]

    operations = [migrations.RunPython(correggi, migrations.RunPython.noop)]
