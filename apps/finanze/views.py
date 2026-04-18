from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q, Count
from django.http import JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json

from .models import ChiusuraCassa, MovimentoCassa, Cassa, ChiusuraCassaAutomatica, QuadraturaGiornaliera
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

    # Raggruppa servizi per categoria
    servizi_per_categoria = {}
    for servizio_nome, servizio_data in servizi_stats.items():
        cat_nome = servizio_data['categoria']
        if cat_nome not in servizi_per_categoria:
            servizi_per_categoria[cat_nome] = []
        servizi_per_categoria[cat_nome].append(servizio_data)

    # Ordina servizi dentro ogni categoria per quantità
    for cat_nome in servizi_per_categoria:
        servizi_per_categoria[cat_nome].sort(key=lambda x: x['quantita'], reverse=True)

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
        'servizi_per_categoria': servizi_per_categoria,
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


@login_required
@user_passes_test(is_staff_user)
def analisi_vendite(request):
    """Analisi vendite con filtri periodo: giornaliero, settimanale, mensile, personalizzato"""
    oggi = timezone.now().date()

    # Ottieni parametri filtro
    periodo_tipo = request.GET.get('periodo', 'giornaliero')
    data_inizio_str = request.GET.get('data_inizio')
    data_fine_str = request.GET.get('data_fine')

    # Calcola range date in base al periodo
    if periodo_tipo == 'giornaliero':
        data_inizio = oggi
        data_fine = oggi
    elif periodo_tipo == 'settimanale':
        # Settimana corrente (lunedì - domenica)
        data_inizio = oggi - timedelta(days=oggi.weekday())
        data_fine = data_inizio + timedelta(days=6)
    elif periodo_tipo == 'mensile':
        # Mese corrente
        data_inizio = oggi.replace(day=1)
        # Ultimo giorno del mese
        if oggi.month == 12:
            data_fine = oggi.replace(day=31)
        else:
            data_fine = (oggi.replace(month=oggi.month + 1, day=1) - timedelta(days=1))
    elif periodo_tipo == 'personalizzato':
        # Range personalizzato
        if data_inizio_str and data_fine_str:
            try:
                data_inizio = datetime.strptime(data_inizio_str, '%Y-%m-%d').date()
                data_fine = datetime.strptime(data_fine_str, '%Y-%m-%d').date()
            except ValueError:
                data_inizio = oggi
                data_fine = oggi
        else:
            data_inizio = oggi
            data_fine = oggi
    else:
        data_inizio = oggi
        data_fine = oggi

    # Pagamenti nel periodo
    pagamenti_periodo = Pagamento.objects.filter(
        data_pagamento__date__gte=data_inizio,
        data_pagamento__date__lte=data_fine
    ).select_related('ordine', 'operatore')

    # Totali per metodo
    totale_contanti = pagamenti_periodo.filter(metodo='contanti').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')
    totale_carte = pagamenti_periodo.filter(metodo='carta').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')
    totale_bancomat = pagamenti_periodo.filter(metodo='bancomat').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')
    totale_bonifici = pagamenti_periodo.filter(metodo='bonifico').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')
    totale_assegni = pagamenti_periodo.filter(metodo='assegno').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')
    totale_abbonamenti = pagamenti_periodo.filter(metodo='abbonamento').aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')

    # Totale generale
    totale_periodo = (
        totale_contanti + totale_carte + totale_bancomat +
        totale_bonifici + totale_assegni + totale_abbonamenti
    )

    # Statistiche generali
    num_transazioni = pagamenti_periodo.count()
    scontrino_medio = totale_periodo / num_transazioni if num_transazioni > 0 else Decimal('0.00')

    # Calcola range date
    giorni_periodo = (data_fine - data_inizio).days + 1
    media_giornaliera = totale_periodo / giorni_periodo if giorni_periodo > 0 else Decimal('0.00')

    # Analisi per categoria
    items_periodo = ItemOrdine.objects.filter(
        ordine__data_ora__date__gte=data_inizio,
        ordine__data_ora__date__lte=data_fine,
        ordine__stato_pagamento='pagato'
    ).select_related('servizio_prodotto__categoria')

    categorie_stats = {}
    for item in items_periodo:
        categoria_nome = item.servizio_prodotto.categoria.nome
        if categoria_nome not in categorie_stats:
            categorie_stats[categoria_nome] = {
                'nome': categoria_nome,
                'quantita': 0,
                'fatturato': Decimal('0.00')
            }
        categorie_stats[categoria_nome]['quantita'] += item.quantita
        categorie_stats[categoria_nome]['fatturato'] += item.subtotale

    categorie_report = sorted(
        categorie_stats.values(),
        key=lambda x: x['fatturato'],
        reverse=True
    )

    # Analisi servizi per categoria
    servizi_stats = {}
    for item in items_periodo:
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

    # Raggruppa per categoria
    servizi_per_categoria = {}
    for servizio_nome, servizio_data in servizi_stats.items():
        cat_nome = servizio_data['categoria']
        if cat_nome not in servizi_per_categoria:
            servizi_per_categoria[cat_nome] = []
        servizi_per_categoria[cat_nome].append(servizio_data)

    for cat_nome in servizi_per_categoria:
        servizi_per_categoria[cat_nome].sort(key=lambda x: x['quantita'], reverse=True)

    # Confronto con periodo precedente
    data_inizio_precedente = data_inizio - timedelta(days=giorni_periodo)
    data_fine_precedente = data_inizio - timedelta(days=1)

    pagamenti_precedente = Pagamento.objects.filter(
        data_pagamento__date__gte=data_inizio_precedente,
        data_pagamento__date__lte=data_fine_precedente
    )
    totale_precedente = pagamenti_precedente.aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')

    variazione_percentuale = None
    if totale_precedente > 0:
        variazione_percentuale = ((totale_periodo - totale_precedente) / totale_precedente) * 100

    # Analisi giornaliera (per grafici trend)
    giorni_trend = []
    data_corrente = data_inizio
    while data_corrente <= data_fine:
        totale_giorno = Pagamento.objects.filter(
            data_pagamento__date=data_corrente
        ).aggregate(Sum('importo'))['importo__sum'] or Decimal('0.00')

        giorni_trend.append({
            'data': data_corrente,
            'totale': totale_giorno
        })
        data_corrente += timedelta(days=1)

    context = {
        'periodo_tipo': periodo_tipo,
        'data_inizio': data_inizio,
        'data_fine': data_fine,
        'giorni_periodo': giorni_periodo,
        'totale_periodo': totale_periodo,
        'num_transazioni': num_transazioni,
        'scontrino_medio': scontrino_medio,
        'media_giornaliera': media_giornaliera,
        'totale_contanti': totale_contanti,
        'totale_carte': totale_carte,
        'totale_bancomat': totale_bancomat,
        'totale_bonifici': totale_bonifici,
        'totale_assegni': totale_assegni,
        'totale_abbonamenti': totale_abbonamenti,
        'categorie_report': categorie_report,
        'servizi_per_categoria': servizi_per_categoria,
        'totale_precedente': totale_precedente,
        'variazione_percentuale': variazione_percentuale,
        'giorni_trend': giorni_trend,
    }

    return render(request, 'finanze/analisi_vendite.html', context)


# ---------------------------------------------------------------------------
# Chiusure casse automatiche (cambia gettoni, portali)
# ---------------------------------------------------------------------------

def _parse_data(request, default=None):
    data_str = request.GET.get('data')
    if data_str:
        try:
            return datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    return default or timezone.now().date()


@login_required
@user_passes_test(is_staff_user)
def chiusura_automatica_list(request):
    """Lista casse automatiche del giorno con stato chiusura."""
    data = _parse_data(request)
    casse_auto = Cassa.objects.filter(tipo='automatica', attiva=True).order_by('ordine')

    # Mappa chiusure esistenti per il giorno
    chiusure = {
        c.cassa_id: c for c in ChiusuraCassaAutomatica.objects.filter(data=data)
    }

    casse_data = []
    totale_incasso = Decimal('0.00')
    totale_wash_cycles = 0

    for cassa in casse_auto:
        chiusura = chiusure.get(cassa.pk)
        if chiusura:
            totale_incasso += chiusura.incasso_totale
            if chiusura.wash_cycles:
                totale_wash_cycles += chiusura.wash_cycles
        casse_data.append({
            'cassa': cassa,
            'chiusura': chiusura,
        })

    context = {
        'data': data,
        'casse_data': casse_data,
        'totale_incasso': totale_incasso,
        'totale_wash_cycles': totale_wash_cycles,
    }
    return render(request, 'finanze/chiusura_automatica_list.html', context)


@login_required
@user_passes_test(is_staff_user)
def chiusura_automatica_create(request, cassa_id):
    """Form di chiusura per una cassa automatica."""
    cassa = get_object_or_404(Cassa, pk=cassa_id, tipo='automatica', attiva=True)
    data = _parse_data(request)

    # Se esiste gia una chiusura per oggi, redirect al detail
    esistente = ChiusuraCassaAutomatica.objects.filter(cassa=cassa, data=data).first()
    if esistente:
        return redirect('finanze:chiusura_automatica_detail', pk=esistente.pk)

    if request.method == 'POST':
        try:
            chiusura = ChiusuraCassaAutomatica(
                cassa=cassa,
                data=data,
                operatore=request.user,
                incasso_totale=Decimal(request.POST.get('incasso_totale') or '0'),
                incasso_ricarica=Decimal(request.POST.get('incasso_ricarica') or '0'),
                vendita_contante=Decimal(request.POST.get('vendita_contante') or '0'),
                vendita_non_contante=Decimal(request.POST.get('vendita_non_contante') or '0'),
                resto_erogato_reale=Decimal(request.POST.get('resto_erogato_reale') or '0'),
                note=request.POST.get('note', ''),
            )
            if cassa.tracking_washcycles:
                wc = request.POST.get('wash_cycles')
                if wc:
                    chiusura.wash_cycles = int(wc)
            chiusura.save()
            messages.success(request, f"Chiusura {cassa} del {data.strftime('%d/%m/%Y')} salvata.")
            return redirect('finanze:chiusura_automatica_detail', pk=chiusura.pk)
        except (ValueError, TypeError) as e:
            messages.error(request, f"Dati non validi: {e}")

    context = {
        'cassa': cassa,
        'data': data,
        'mode': 'create',
    }
    return render(request, 'finanze/chiusura_automatica_form.html', context)


@login_required
@user_passes_test(is_staff_user)
def chiusura_automatica_detail(request, pk):
    """Dettaglio chiusura automatica."""
    chiusura = get_object_or_404(ChiusuraCassaAutomatica, pk=pk)
    return render(request, 'finanze/chiusura_automatica_detail.html', {
        'chiusura': chiusura,
    })


@login_required
@user_passes_test(is_staff_user)
def chiusura_automatica_edit(request, pk):
    """Modifica chiusura automatica (solo se non confermata)."""
    chiusura = get_object_or_404(ChiusuraCassaAutomatica, pk=pk)
    if chiusura.confermata:
        messages.warning(request, "Chiusura confermata, non modificabile.")
        return redirect('finanze:chiusura_automatica_detail', pk=pk)

    if request.method == 'POST':
        try:
            chiusura.incasso_totale = Decimal(request.POST.get('incasso_totale') or '0')
            chiusura.incasso_ricarica = Decimal(request.POST.get('incasso_ricarica') or '0')
            chiusura.vendita_contante = Decimal(request.POST.get('vendita_contante') or '0')
            chiusura.vendita_non_contante = Decimal(request.POST.get('vendita_non_contante') or '0')
            chiusura.resto_erogato_reale = Decimal(request.POST.get('resto_erogato_reale') or '0')
            chiusura.note = request.POST.get('note', '')
            if chiusura.cassa.tracking_washcycles:
                wc = request.POST.get('wash_cycles')
                chiusura.wash_cycles = int(wc) if wc else None
            chiusura.save()
            messages.success(request, "Chiusura aggiornata.")
            return redirect('finanze:chiusura_automatica_detail', pk=pk)
        except (ValueError, TypeError) as e:
            messages.error(request, f"Dati non validi: {e}")

    return render(request, 'finanze/chiusura_automatica_form.html', {
        'chiusura': chiusura,
        'cassa': chiusura.cassa,
        'data': chiusura.data,
        'mode': 'edit',
    })


@login_required
@user_passes_test(is_staff_user)
def chiusura_automatica_conferma(request, pk):
    """Conferma chiusura automatica (blocco modifiche)."""
    chiusura = get_object_or_404(ChiusuraCassaAutomatica, pk=pk)
    if request.method == 'POST':
        password = request.POST.get('password', '')
        if not request.user.check_password(password):
            messages.error(request, "Password non corretta.")
            return redirect('finanze:chiusura_automatica_detail', pk=pk)
        chiusura.confermata = True
        chiusura.save(update_fields=['confermata'])
        messages.success(request, "Chiusura confermata e bloccata.")
    return redirect('finanze:chiusura_automatica_detail', pk=pk)


@login_required
@user_passes_test(is_staff_user)
def report_giornata(request):
    """Report aggregato del giorno: cassa servito + tutte le automatiche + analisi."""
    data = _parse_data(request)

    # Cassa servito
    cassa_servito = ChiusuraCassa.objects.filter(data=data).first()
    if cassa_servito:
        cassa_servito.ricalcola_totali()

    # Chiusure automatiche (include registratore servito)
    chiusure_auto = ChiusuraCassaAutomatica.objects.filter(data=data).select_related('cassa').order_by('cassa__ordine')

    # Aggregati casse automatiche (solo NON registratore, ovvero self service)
    agg = {
        'incasso_totale': Decimal('0.00'),
        'incasso_ricarica': Decimal('0.00'),
        'incasso_vendita': Decimal('0.00'),
        'vendita_contante': Decimal('0.00'),
        'vendita_non_contante': Decimal('0.00'),
        'vendita_totale': Decimal('0.00'),
        'resto_erogato_teorico': Decimal('0.00'),
        'wash_cycles': 0,
    }

    # Aggregati per categoria (portali, cambia gettoni, registratore)
    totale_portali = Decimal('0.00')          # casse con tracking_washcycles=True
    totale_cambia_gettoni = Decimal('0.00')   # casse automatiche normali (no portali, no registratore)
    totale_registratore = Decimal('0.00')     # totale scontrino registratore servito
    wash_cycles_portali = 0
    chiusura_registratore = None

    for c in chiusure_auto:
        if c.cassa.modalita_registratore:
            totale_registratore += c.incasso_totale
            chiusura_registratore = c
            continue

        # Non-registratore: contribuisce all'aggregato self-service
        agg['incasso_totale'] += c.incasso_totale
        agg['incasso_ricarica'] += c.incasso_ricarica
        agg['incasso_vendita'] += c.incasso_vendita
        agg['vendita_contante'] += c.vendita_contante
        agg['vendita_non_contante'] += c.vendita_non_contante
        agg['vendita_totale'] += c.vendita_totale
        agg['resto_erogato_teorico'] += c.resto_erogato_teorico
        if c.wash_cycles:
            agg['wash_cycles'] += c.wash_cycles

        if c.cassa.tracking_washcycles:
            totale_portali += c.vendita_totale
            if c.wash_cycles:
                wash_cycles_portali += c.wash_cycles
        else:
            totale_cambia_gettoni += c.vendita_totale

    totale_self_service = totale_portali + totale_cambia_gettoni

    # Totale servito PAGATO = da pagamenti POS (usato per quadratura: confronto
    # con contanti+carte realmente incassati).
    if cassa_servito:
        totale_servito_pagato = cassa_servito.totale_incassi_giornalieri
    else:
        # Fallback: somma diretta dei pagamenti del giorno se non c'e ChiusuraCassa
        totale_servito_pagato = Pagamento.objects.filter(
            data_pagamento__date=data
        ).aggregate(s=Sum('importo'))['s'] or Decimal('0.00')

    # Totale servito ORDINATO = ordini del giorno esclusi annullati (include
    # anche non pagati/parziali/differiti). Usato nel badge "Totale servito".
    _ordini_giorno_qs = Ordine.objects.filter(data_ora__date=data).exclude(stato='annullato')
    totale_servito_ordinato = _ordini_giorno_qs.aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00')

    # Totale giornata = servito ordinato (include non pagati) + self service
    totale_giornata = totale_servito_ordinato + totale_self_service

    # Compat per la sezione quadratura sottostante
    totale_servito = totale_servito_pagato

    # ==================== CORRISPETTIVI (registratore + automatiche) ====================
    # Totale corrispettivi = totale registratore + vendita totale casse automatiche
    # (questi sono i documenti fiscali emessi: scontrino + chiusure cassa).
    # IVA 22% inclusa nel lordo.
    totale_corrispettivi = totale_registratore + agg['vendita_totale']
    IVA_RATE = Decimal('0.22')
    if totale_corrispettivi > 0:
        imponibile_corrispettivi = (totale_corrispettivi / (Decimal('1') + IVA_RATE)).quantize(Decimal('0.01'))
        iva_corrispettivi = totale_corrispettivi - imponibile_corrispettivi
    else:
        imponibile_corrispettivi = Decimal('0.00')
        iva_corrispettivi = Decimal('0.00')

    # ==================== ORDINI NON INCASSATI ====================
    ordini_non_pagati = Ordine.objects.filter(
        data_ora__date=data,
        stato_pagamento__in=['non_pagato', 'parziale', 'differito'],
    ).exclude(stato='annullato').select_related('cliente').order_by('-data_ora')

    totale_crediti = Decimal('0.00')
    ordini_non_pagati_list = []
    for o in ordini_non_pagati:
        saldo = o.saldo_dovuto
        totale_crediti += saldo
        ordini_non_pagati_list.append({
            'ordine': o,
            'saldo': saldo,
            'cliente': str(o.cliente) if o.cliente else 'Anonimo',
        })

    # ==================== ORDINI E PAGAMENTI DEL GIORNO ====================
    ordini_giorno = Ordine.objects.filter(
        data_ora__date=data,
    ).exclude(stato='annullato')
    num_ordini = ordini_giorno.count()
    totale_ordinato = ordini_giorno.aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00')

    pagamenti_giorno = Pagamento.objects.filter(
        data_pagamento__date=data,
    ).select_related('ordine')
    num_transazioni = pagamenti_giorno.count()
    # Scontrino medio basato sull'effettivamente incassato (pagato + self service)
    _totale_incassato = totale_servito_pagato + totale_self_service
    scontrino_medio = _totale_incassato / num_transazioni if num_transazioni > 0 else Decimal('0.00')

    # ==================== METODI DI PAGAMENTO ====================
    metodi_totali = {}
    for p in pagamenti_giorno:
        m = p.metodo or 'altro'
        metodi_totali[m] = metodi_totali.get(m, Decimal('0.00')) + p.importo

    # Label piu leggibili
    metodi_labels = {
        'contanti': 'Contanti', 'carta': 'Carta', 'bancomat': 'Bancomat',
        'bonifico': 'Bonifico', 'assegno': 'Assegno', 'abbonamento': 'Abbonamento',
        'altro': 'Altro',
    }
    metodi_chart = sorted(
        [(metodi_labels.get(k, k.title()), float(v)) for k, v in metodi_totali.items()],
        key=lambda x: -x[1]
    )

    # ==================== TOP SERVIZI PER FATTURATO ====================
    items_pagati = ItemOrdine.objects.filter(
        ordine__data_ora__date=data,
        ordine__stato_pagamento='pagato',
    ).select_related('servizio_prodotto__categoria')

    servizi_stats = {}
    categorie_stats = {}
    for item in items_pagati:
        sp = item.servizio_prodotto
        nome = sp.titolo
        servizi_stats.setdefault(nome, {
            'nome': nome,
            'categoria': sp.categoria.nome if sp.categoria else '—',
            'quantita': 0,
            'fatturato': Decimal('0.00'),
        })
        servizi_stats[nome]['quantita'] += item.quantita
        servizi_stats[nome]['fatturato'] += item.subtotale

        if sp.categoria:
            cn = sp.categoria.nome
            categorie_stats.setdefault(cn, {
                'nome': cn, 'quantita': 0, 'fatturato': Decimal('0.00'),
            })
            categorie_stats[cn]['quantita'] += item.quantita
            categorie_stats[cn]['fatturato'] += item.subtotale

    top_servizi = sorted(servizi_stats.values(), key=lambda x: -x['fatturato'])[:10]
    top_categorie = sorted(categorie_stats.values(), key=lambda x: -x['fatturato'])

    # Chart data servizi
    servizi_chart = [
        {'nome': s['nome'], 'fatturato': float(s['fatturato']), 'quantita': s['quantita']}
        for s in top_servizi
    ]
    categorie_chart = [
        {'nome': c['nome'], 'fatturato': float(c['fatturato']), 'quantita': c['quantita']}
        for c in top_categorie
    ]

    # ==================== TREND ORARIO ====================
    # Suddivide i pagamenti in 24 bucket orari
    orario_buckets = [0.0] * 24
    orario_counts = [0] * 24
    for p in pagamenti_giorno:
        h = p.data_pagamento.hour
        orario_buckets[h] += float(p.importo)
        orario_counts[h] += 1

    # Ora di picco
    ora_picco = None
    if any(orario_buckets):
        ora_picco_h = orario_buckets.index(max(orario_buckets))
        ora_picco = f"{ora_picco_h:02d}:00"

    # ==================== CONFRONTO GIORNO PRECEDENTE ====================
    ieri = data - timedelta(days=1)
    pag_ieri = Pagamento.objects.filter(data_pagamento__date=ieri).aggregate(s=Sum('importo'))['s'] or Decimal('0.00')
    chiusure_auto_ieri = ChiusuraCassaAutomatica.objects.filter(data=ieri).aggregate(s=Sum('incasso_totale'))['s'] or Decimal('0.00')
    totale_ieri = pag_ieri + chiusure_auto_ieri
    variazione_pct = None
    if totale_ieri > 0:
        variazione_pct = float((totale_giornata - totale_ieri) / totale_ieri * 100)

    # ==================== KPI GENERALI ====================
    kpis = {
        'num_ordini': num_ordini,
        'num_transazioni': num_transazioni,
        'scontrino_medio': scontrino_medio,
        'ora_picco': ora_picco or '—',
        'num_non_pagati': len(ordini_non_pagati_list),
        'totale_crediti': totale_crediti,
        'totale_ieri': totale_ieri,
        'variazione_pct': variazione_pct,
    }

    # ==================== QUADRATURA GIORNALIERA COMPLESSIVA ====================
    # L'operatore scassetta TUTTE le casse automatiche + registratore,
    # conta tutti i contanti insieme + lettore carte POS servito.
    # Totale reale vs Totale teorico = vendita self-service + totale servito POS.
    quadratura_obj = QuadraturaGiornaliera.objects.filter(data=data).first()

    vendita_self_service = agg['vendita_totale']
    totale_teorico = vendita_self_service + totale_servito

    # Fondo cassa iniziale del servito (va sottratto dal reale perche gia presente
    # nella cassa all'apertura, non rappresenta incasso della giornata).
    fondo_cassa_iniziale = cassa_servito.fondo_cassa_iniziale if cassa_servito else Decimal('0.00')

    if quadratura_obj:
        lordo_reale = quadratura_obj.contanti_totali + quadratura_obj.lettore_carte_servito
        totale_reale = lordo_reale - fondo_cassa_iniziale
        differenza_quadratura = totale_reale - totale_teorico
        if abs(differenza_quadratura) < Decimal('0.50'):
            stato_quadratura = 'ok'
        elif differenza_quadratura < 0:
            stato_quadratura = 'mancante'
        else:
            stato_quadratura = 'eccedente'
    else:
        lordo_reale = None
        totale_reale = None
        differenza_quadratura = None
        stato_quadratura = 'non_rilevato'

    quadratura = {
        'obj': quadratura_obj,
        'vendita_self_service': vendita_self_service,
        'totale_servito': totale_servito,
        'totale_teorico': totale_teorico,
        'fondo_cassa_iniziale': fondo_cassa_iniziale,
        'lordo_reale': lordo_reale,
        'totale_reale': totale_reale,
        'differenza': differenza_quadratura,
        'stato': stato_quadratura,
    }

    # ==================== PIE CHART TOTALE GIORNATA ====================
    # Split: portali / cambia gettoni / servito (ordinato, include non pagati)
    giornata_split = [
        {'label': 'Servito', 'value': float(totale_servito_ordinato), 'color': '#10b981'},
        {'label': 'Portali', 'value': float(totale_portali), 'color': '#3b82f6'},
        {'label': 'Cambia gettoni', 'value': float(totale_cambia_gettoni), 'color': '#f59e0b'},
    ]
    giornata_split = [s for s in giornata_split if s['value'] > 0]

    context = {
        'data': data,
        'cassa_servito': cassa_servito,
        'chiusure_auto': chiusure_auto,
        'agg': agg,
        'totale_giornata': totale_giornata,
        'totale_ordinato': totale_ordinato,
        # Nuovi totali per categoria (richiesti)
        'totale_portali': totale_portali,
        'totale_cambia_gettoni': totale_cambia_gettoni,
        'totale_self_service': totale_self_service,
        'totale_servito': totale_servito,
        'totale_servito_pagato': totale_servito_pagato,
        'totale_servito_ordinato': totale_servito_ordinato,
        'totale_non_pagato': totale_crediti,
        'totale_corrispettivi': totale_corrispettivi,
        'imponibile_corrispettivi': imponibile_corrispettivi,
        'iva_corrispettivi': iva_corrispettivi,
        'giornata_split_json': json.dumps(giornata_split),
        'wash_cycles_portali': wash_cycles_portali,
        'chiusura_registratore': chiusura_registratore,
        # Quadratura contanti
        'quadratura': quadratura,
        # Resto
        'kpis': kpis,
        'ordini_non_pagati': ordini_non_pagati_list,
        'top_servizi': top_servizi,
        'top_categorie': top_categorie,
        'metodi_chart_json': json.dumps(metodi_chart),
        'servizi_chart_json': json.dumps(servizi_chart),
        'categorie_chart_json': json.dumps(categorie_chart),
        'orario_buckets_json': json.dumps(orario_buckets),
        'orario_counts_json': json.dumps(orario_counts),
    }
    return render(request, 'finanze/report_giornata.html', context)


@login_required
@user_passes_test(is_staff_user)
def quadratura_form(request):
    """Form per inserire/modificare la quadratura giornaliera complessiva."""
    data = _parse_data(request)
    quadratura = QuadraturaGiornaliera.objects.filter(data=data).first()
    cassa_servito = ChiusuraCassa.objects.filter(data=data).first()
    fondo_cassa_iniziale = cassa_servito.fondo_cassa_iniziale if cassa_servito else Decimal('0.00')

    if request.method == 'POST':
        try:
            contanti = Decimal(request.POST.get('contanti_totali') or '0')
            lettore = Decimal(request.POST.get('lettore_carte_servito') or '0')
            note = request.POST.get('note', '')
            QuadraturaGiornaliera.objects.update_or_create(
                data=data,
                defaults={
                    'contanti_totali': contanti,
                    'lettore_carte_servito': lettore,
                    'note': note,
                    'operatore': request.user,
                },
            )
            messages.success(request, f"Quadratura del {data.strftime('%d/%m/%Y')} salvata.")
            from django.urls import reverse
            return redirect(f"{reverse('finanze:report_giornata')}?data={data.strftime('%Y-%m-%d')}")
        except (ValueError, TypeError) as e:
            messages.error(request, f"Dati non validi: {e}")

    return render(request, 'finanze/quadratura_form.html', {
        'data': data,
        'quadratura': quadratura,
        'fondo_cassa_iniziale': fondo_cassa_iniziale,
    })
