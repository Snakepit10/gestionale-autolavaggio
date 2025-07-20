from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from .models import ItemOrdine, Ordine, Pagamento
from apps.core.models import MovimentoScorte
from apps.clienti.models import PuntiFedelta, MovimentoPunti


@receiver(post_save, sender=ItemOrdine)
def aggiorna_scorte_prodotto(sender, instance, created, **kwargs):
    """Aggiorna automaticamente le scorte quando viene creato un ItemOrdine per un prodotto"""
    if created and instance.servizio_prodotto.tipo == 'prodotto':
        prodotto = instance.servizio_prodotto
        
        # Solo se il prodotto ha scorte gestite (non illimitate)
        if prodotto.quantita_disponibile > 0:
            with transaction.atomic():
                quantita_prima = prodotto.quantita_disponibile
                quantita_scarico = instance.quantita
                
                # Aggiorna la quantità disponibile
                prodotto.quantita_disponibile = max(0, quantita_prima - quantita_scarico)
                prodotto.save(update_fields=['quantita_disponibile'])
                
                # Registra il movimento di scorte
                MovimentoScorte.objects.create(
                    prodotto=prodotto,
                    tipo='scarico',
                    quantita=-quantita_scarico,
                    quantita_prima=quantita_prima,
                    quantita_dopo=prodotto.quantita_disponibile,
                    riferimento_ordine=instance.ordine,
                    nota=f'Vendita - Ordine {instance.ordine.numero_progressivo}',
                    operatore=instance.ordine.operatore
                )


@receiver(post_save, sender=Ordine)
def gestisci_punti_fedelta(sender, instance, created, **kwargs):
    """Gestisce l'accumulo di punti fedeltà quando un ordine viene pagato"""
    if not created and instance.cliente and instance.is_pagato:
        # Verifica se i punti sono già stati assegnati
        if not MovimentoPunti.objects.filter(ordine=instance, tipo='accumulo').exists():
            punti_da_assegnare = instance.calcola_punti_fedelta()
            
            if punti_da_assegnare > 0:
                # Ottieni o crea il record punti fedeltà
                punti_fedelta, created = PuntiFedelta.objects.get_or_create(
                    cliente=instance.cliente,
                    defaults={'punti_totali': 0, 'punti_utilizzati': 0}
                )
                
                # Aggiorna i punti totali
                punti_fedelta.punti_totali += punti_da_assegnare
                punti_fedelta.save()
                
                # Registra il movimento
                MovimentoPunti.objects.create(
                    cliente=instance.cliente,
                    tipo='accumulo',
                    punti=punti_da_assegnare,
                    descrizione=f'Acquisto ordine {instance.numero_progressivo}',
                    ordine=instance
                )
                
                # Aggiorna il campo nell'ordine
                instance.punti_fedelta_generati = punti_da_assegnare
                instance.save(update_fields=['punti_fedelta_generati'])


@receiver(pre_save, sender=Ordine)
def aggiorna_importo_pagato(sender, instance, **kwargs):
    """Aggiorna automaticamente l'importo pagato basato sui pagamenti registrati"""
    if instance.pk:  # Solo per ordini esistenti
        from django.db.models import Sum
        importo_totale = instance.pagamenti.aggregate(Sum('importo'))['importo__sum'] or 0
        instance.importo_pagato = importo_totale


@receiver(post_save, sender=Pagamento)
def aggiorna_ordine_dopo_pagamento(sender, instance, created, **kwargs):
    """Aggiorna l'ordine quando viene salvato un pagamento"""
    if instance.ordine_id:
        # Forza il ricalcolo dell'importo pagato e dello stato
        ordine = instance.ordine
        from django.db.models import Sum
        importo_totale = ordine.pagamenti.aggregate(Sum('importo'))['importo__sum'] or 0
        ordine.importo_pagato = importo_totale
        
        # Aggiorna lo stato del pagamento
        if ordine.saldo_dovuto <= 0:
            ordine.stato_pagamento = 'pagato'
        elif ordine.importo_pagato > 0:
            ordine.stato_pagamento = 'parziale'
        else:
            ordine.stato_pagamento = 'non_pagato'
        
        ordine.save(update_fields=['importo_pagato', 'stato_pagamento'])


@receiver(post_delete, sender=Pagamento)
def aggiorna_ordine_dopo_cancellazione_pagamento(sender, instance, **kwargs):
    """Aggiorna l'ordine quando viene cancellato un pagamento"""
    if instance.ordine_id:
        # Forza il ricalcolo dell'importo pagato e dello stato
        ordine = instance.ordine
        from django.db.models import Sum
        importo_totale = ordine.pagamenti.aggregate(Sum('importo'))['importo__sum'] or 0
        ordine.importo_pagato = importo_totale
        
        # Aggiorna lo stato del pagamento
        if ordine.saldo_dovuto <= 0:
            ordine.stato_pagamento = 'pagato'
        elif ordine.importo_pagato > 0:
            ordine.stato_pagamento = 'parziale'
        else:
            ordine.stato_pagamento = 'non_pagato'
        
        ordine.save(update_fields=['importo_pagato', 'stato_pagamento'])