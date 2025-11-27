from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q, Count
from django.http import JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json

from .models import ChiusuraCassa, MovimentoCassa
from apps.ordini.models import Pagamento, Ordine, ItemOrdine
from apps.core.models import Categoria


def is_staff_user(user):
    return user.is_staff


@login_required
@user_passes_test(is_staff_user)
def chiusura_cassa_dashboard(request):
    """Dashboard principale chiusura cassa"""
    oggi = timezone.now().date()

    # Verifica se esiste apertura per oggi
    chiusura_oggi = ChiusuraCassa.objects.filter(data=oggi).first()

    context = {
        'oggi': oggi,
        'chiusura_oggi': chiusura_oggi,
    }

    if chiusura_oggi:
        # Calcola dati per la chiusura corrente
        chiusura_oggi.ricalcola_totali()

        # Ottieni movimenti e pagamenti del giorno
        movimenti = chiusura_oggi.movimenti.all().order_by('-data_ora')
        pagamenti = Pagamento.objects.filter(
            data_pagamento__date=oggi
        ).select_related('ordine', 'operatore').order_by('-data_pagamento')

        # Servizi non pagati
        servizi_non_pagati = Ordine.objects.filter(
            data_ora__date=oggi,
            stato_pagamento__in=['non_pagato', 'parziale']
        ).select_related('cliente').order_by('-data_ora')

        context.update({
            'movimenti': movimenti,
            'pagamenti': pagamenti,
            'servizi_non_pagati': servizi_non_pagati,
        })

    return render(request, 'finanze/chiusura_cassa_dashboard.html', context)


@login_required
@user_passes_test(is_staff_user)
def apri_cassa(request):
    """Apre la cassa per la giornata"""
    oggi = timezone.now().date()

    # Verifica se già aperta
    if ChiusuraCassa.objects.filter(data=oggi).exists():
        messages.warning(request, "La cassa è già stata aperta oggi.")
        return redirect('finanze:dashboard')

    if request.method == 'POST':
        fondo_iniziale = request.POST.get('fondo_cassa_iniziale')
        note = request.POST.get('note_apertura', '')
        copia_da_ieri = request.POST.get('copia_da_ieri') == 'on'

        try:
            fondo_iniziale = Decimal(fondo_iniziale)

            # Se richiesto, copia il fondo dalla chiusura di ieri
            if copia_da_ieri:
                ieri = oggi - timedelta(days=1)
                chiusura_ieri = ChiusuraCassa.objects.filter(
                    data=ieri,
                    stato='chiusa'
                ).first()
                if chiusura_ieri and chiusura_ieri.conteggio_cassa_reale:
                    fondo_iniziale = chiusura_ieri.conteggio_cassa_reale

            chiusura = ChiusuraCassa.objects.create(
                data=oggi,
                operatore_apertura=request.user,
                fondo_cassa_iniziale=fondo_iniziale,
                note_apertura=note,
                stato='aperta'
            )

            messages.success(
                request,
                f"Cassa aperta con successo. Fondo iniziale: €{fondo_iniziale:.2f}"
            )
            return redirect('finanze:dashboard')

        except (ValueError, TypeError):
            messages.error(request, "Importo fondo cassa non valido.")

    # Ottieni ultima chiusura per suggerimento
    ultima_chiusura = ChiusuraCassa.objects.filter(
        stato='chiusa'
    ).order_by('-data').first()

    context = {
        'ultima_chiusura': ultima_chiusura,
    }

    return render(request, 'finanze/apri_cassa.html', context)


@login_required
@user_passes_test(is_staff_user)
def chiudi_cassa(request):
    """Chiude la cassa per la giornata"""
    oggi = timezone.now().date()
    chiusura = get_object_or_404(ChiusuraCassa, data=oggi, stato='aperta')

    if request.method == 'POST':
        conteggio_reale = request.POST.get('conteggio_cassa_reale')
        note = request.POST.get('note_chiusura', '')

        try:
            conteggio_reale = Decimal(conteggio_reale)

            # Ricalcola totali prima di chiudere
            chiusura.ricalcola_totali()

            # Chiudi la cassa
            chiusura.chiudi(
                conteggio_reale=conteggio_reale,
                note=note,
                operatore=request.user
            )

            diff = chiusura.differenza_cassa
            if abs(diff) < Decimal('0.50'):
                msg_tipo = messages.success
                msg = f"Cassa chiusa con successo. Differenza: €{diff:.2f} (ok)"
            elif diff < 0:
                msg_tipo = messages.warning
                msg = f"Cassa chiusa. ATTENZIONE: mancano €{abs(diff):.2f}"
            else:
                msg_tipo = messages.warning
                msg = f"Cassa chiusa. ATTENZIONE: eccedenza di €{diff:.2f}"

            msg_tipo(request, msg)
            return redirect('finanze:conferma_chiusura', chiusura_id=chiusura.id)

        except (ValueError, TypeError):
            messages.error(request, "Importo conteggio non valido.")

    # Ricalcola totali per mostrare dati aggiornati
    chiusura.ricalcola_totali()

    return render(request, 'finanze/chiudi_cassa.html', {'chiusura': chiusura})


@login_required
@user_passes_test(is_staff_user)
def conferma_chiusura(request, chiusura_id):
    """Conferma definitivamente la chiusura (con PIN/password)"""
    chiusura = get_object_or_404(ChiusuraCassa, id=chiusura_id, stato='chiusa')

    if chiusura.confermata:
        messages.info(request, "Questa chiusura è già stata confermata.")
        return redirect('finanze:dettaglio_chiusura', chiusura_id=chiusura.id)

    if request.method == 'POST':
        # Verifica password utente
        password = request.POST.get('password')
        if request.user.check_password(password):
            chiusura.conferma_chiusura()
            messages.success(
                request,
                "Chiusura cassa confermata e bloccata. Non è più possibile modificare i dati."
            )
            return redirect('finanze:dettaglio_chiusura', chiusura_id=chiusura.id)
        else:
            messages.error(request, "Password non corretta.")

    return render(request, 'finanze/conferma_chiusura.html', {'chiusura': chiusura})


@login_required
@user_passes_test(is_staff_user)
def storico_chiusure(request):
    """Elenco storico chiusure cassa"""
    chiusure = ChiusuraCassa.objects.all().order_by('-data')

    # Filtri
    operatore_id = request.GET.get('operatore')
    if operatore_id:
        chiusure = chiusure.filter(
            Q(operatore_apertura_id=operatore_id) |
            Q(operatore_chiusura_id=operatore_id)
        )

    data_da = request.GET.get('data_da')
    data_a = request.GET.get('data_a')
    if data_da:
        chiusure = chiusure.filter(data__gte=data_da)
    if data_a:
        chiusure = chiusure.filter(data__lte=data_a)

    solo_differenze = request.GET.get('solo_differenze')
    if solo_differenze:
        # Filtra chiusure con differenze significative (>0.50€)
        chiusure = [c for c in chiusure if c.differenza_cassa and abs(c.differenza_cassa) >= Decimal('0.50')]

    context = {
        'chiusure': chiusure,
    }

    return render(request, 'finanze/storico_chiusure.html', context)


@login_required
@user_passes_test(is_staff_user)
def dettaglio_chiusura(request, chiusura_id):
    """Dettaglio singola chiusura"""
    chiusura = get_object_or_404(ChiusuraCassa, id=chiusura_id)

    movimenti = chiusura.movimenti.all().order_by('-data_ora')
    pagamenti = Pagamento.objects.filter(
        data_pagamento__date=chiusura.data
    ).select_related('ordine', 'operatore').order_by('-data_pagamento')

    context = {
        'chiusura': chiusura,
        'movimenti': movimenti,
        'pagamenti': pagamenti,
    }

    return render(request, 'finanze/dettaglio_chiusura.html', context)


@login_required
@user_passes_test(is_staff_user)
def aggiungi_movimento(request):
    """Aggiungi movimento di cassa"""
    oggi = timezone.now().date()
    chiusura = get_object_or_404(ChiusuraCassa, data=oggi, stato='aperta')

    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        categoria = request.POST.get('categoria')
        importo = request.POST.get('importo')
        causale = request.POST.get('causale')
        dettagli = request.POST.get('dettagli', '')
        riferimento = request.POST.get('riferimento_documento', '')

        try:
            importo = Decimal(importo)

            movimento = MovimentoCassa.objects.create(
                chiusura_cassa=chiusura,
                tipo=tipo,
                categoria=categoria,
                importo=importo,
                causale=causale,
                dettagli=dettagli,
                riferimento_documento=riferimento,
                operatore=request.user
            )

            messages.success(request, f"Movimento registrato: {movimento}")
            return redirect('finanze:dashboard')

        except (ValueError, TypeError):
            messages.error(request, "Dati non validi.")

    context = {
        'chiusura': chiusura,
        'tipo_choices': MovimentoCassa.TIPO_CHOICES,
        'categoria_choices': MovimentoCassa.CATEGORIA_CHOICES,
    }

    return render(request, 'finanze/aggiungi_movimento.html', context)


@login_required
@user_passes_test(is_staff_user)
def riepilogo_incassi(request):
    """Riepilogo incassi giornalieri con spaccato metodi pagamento"""
    # Data selezionata (default oggi)
    data_str = request.GET.get('data')
    if data_str:
        try:
            data = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            data = timezone.now().date()
    else:
        data = timezone.now().date()

    # Pagamenti del giorno
    pagamenti = Pagamento.objects.filter(
        data_pagamento__date=data
    ).select_related('ordine', 'operatore')

    # Calcola totali per metodo
    totale_contanti = pagamenti.filter(metodo='contanti').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    totale_carte = pagamenti.filter(metodo='carta').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    totale_bancomat = pagamenti.filter(metodo='bancomat').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    totale_bonifici = pagamenti.filter(metodo='bonifico').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    totale_assegni = pagamenti.filter(metodo='assegno').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    totale_abbonamenti = pagamenti.filter(metodo='abbonamento').aggregate(
        totale=Sum('importo')
    )['totale'] or Decimal('0.00')

    # Totale generale
    totale_incassi = (
        totale_contanti + totale_carte + totale_bancomat +
        totale_bonifici + totale_assegni + totale_abbonamenti
    )

    # Numero transazioni
    num_transazioni = pagamenti.count()

    # Scontrino medio
    scontrino_medio = totale_incassi / num_transazioni if num_transazioni > 0 else Decimal('0.00')

    # Confronto con giorno precedente
    ieri = data - timedelta(days=1)
    pagamenti_ieri = Pagamento.objects.filter(data_pagamento__date=ieri)
    totale_ieri = pagamenti_ieri.aggregate(totale=Sum('importo'))['totale'] or Decimal('0.00')

    variazione_percentuale = None
    if totale_ieri > 0:
        variazione_percentuale = ((totale_incassi - totale_ieri) / totale_ieri) * 100

    # Servizi non pagati
    servizi_non_pagati = Ordine.objects.filter(
        data_ora__date__lte=data,
        stato_pagamento__in=['non_pagato', 'parziale']
    ).select_related('cliente').order_by('-data_ora')

    totale_crediti = sum(ordine.saldo_dovuto for ordine in servizi_non_pagati)

    # Dati per grafico (JSON)
    metodi_pagamento_data = {
        'labels': ['Contanti', 'Carte', 'Bancomat', 'Bonifici', 'Assegni', 'Abbonamenti'],
        'data': [
            float(totale_contanti),
            float(totale_carte),
            float(totale_bancomat),
            float(totale_bonifici),
            float(totale_assegni),
            float(totale_abbonamenti)
        ],
        'counts': [
            pagamenti.filter(metodo='contanti').count(),
            pagamenti.filter(metodo='carta').count(),
            pagamenti.filter(metodo='bancomat').count(),
            pagamenti.filter(metodo='bonifico').count(),
            pagamenti.filter(metodo='assegno').count(),
            pagamenti.filter(metodo='abbonamento').count(),
        ]
    }

    # Link chiusura cassa del giorno
    chiusura_cassa = ChiusuraCassa.objects.filter(data=data).first()

    # Report per categoria
    items_giorno = ItemOrdine.objects.filter(
        ordine__data_ora__date=data,
        ordine__stato_pagamento='pagato'
    ).select_related('servizio_prodotto__categoria')

    categorie_stats = {}
    for item in items_giorno:
        categoria_nome = item.servizio_prodotto.categoria.nome
        if categoria_nome not in categorie_stats:
            categorie_stats[categoria_nome] = {
                'nome': categoria_nome,
                'quantita': 0,
                'fatturato': Decimal('0.00')
            }
        categorie_stats[categoria_nome]['quantita'] += item.quantita
        categorie_stats[categoria_nome]['fatturato'] += item.subtotale

    # Ordina per fatturato
    categorie_report = sorted(
        categorie_stats.values(),
        key=lambda x: x['fatturato'],
        reverse=True
    )

    # Servizi più venduti (top 10)
    servizi_stats = {}
    for item in items_giorno:
        servizio_nome = item.servizio_prodotto.titolo
        if servizio_nome not in servizi_stats:
            servizi_stats[servizio_nome] = {
                'nome': servizio_nome,
                'categoria': item.servizio_prodotto.categoria.nome,
                'quantita': 0,
                'fatturato': Decimal('0.00'),
                'prezzo_medio': item.prezzo_unitario
            }
        servizi_stats[servizio_nome]['quantita'] += item.quantita
        servizi_stats[servizio_nome]['fatturato'] += item.subtotale

    # Top 10 per quantità
    top_servizi = sorted(
        servizi_stats.values(),
        key=lambda x: x['quantita'],
        reverse=True
    )[:10]

    # Dati per grafici categorie (JSON)
    categorie_chart_data = {
        'labels': [c['nome'] for c in categorie_report],
        'data': [float(c['fatturato']) for c in categorie_report],
        'counts': [c['quantita'] for c in categorie_report]
    }

    context = {
        'data': data,
        'totale_incassi': totale_incassi,
        'num_transazioni': num_transazioni,
        'scontrino_medio': scontrino_medio,
        'totale_ieri': totale_ieri,
        'variazione_percentuale': variazione_percentuale,
        'totale_contanti': totale_contanti,
        'totale_carte': totale_carte,
        'totale_bancomat': totale_bancomat,
        'totale_bonifici': totale_bonifici,
        'totale_assegni': totale_assegni,
        'totale_abbonamenti': totale_abbonamenti,
        'servizi_non_pagati': servizi_non_pagati,
        'totale_crediti': totale_crediti,
        'metodi_pagamento_data': json.dumps(metodi_pagamento_data),
        'pagamenti': pagamenti,
        'chiusura_cassa': chiusura_cassa,
        'categorie_report': categorie_report,
        'top_servizi': top_servizi,
        'categorie_chart_data': json.dumps(categorie_chart_data),
    }

    return render(request, 'finanze/riepilogo_incassi.html', context)


@login_required
@user_passes_test(is_staff_user)
def marca_pagato(request, ordine_id):
    """Marca un ordine come pagato"""
    ordine = get_object_or_404(Ordine, id=ordine_id)

    if request.method == 'POST':
        metodo = request.POST.get('metodo_pagamento')
        importo = request.POST.get('importo')

        try:
            importo = Decimal(importo)

            # Crea pagamento
            pagamento = Pagamento.objects.create(
                ordine=ordine,
                importo=importo,
                metodo=metodo,
                operatore=request.user,
                nota=f"Pagamento registrato da riepilogo incassi"
            )

            # Aggiorna importo pagato ordine
            ordine.importo_pagato += importo
            ordine.aggiorna_stato_pagamento()

            messages.success(request, f"Pagamento di €{importo} registrato per ordine {ordine.numero_progressivo}")

        except (ValueError, TypeError):
            messages.error(request, "Importo non valido")

    return redirect('finanze:riepilogo_incassi')
