from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q, Count
from django.http import JsonResponse
from datetime import datetime, time, timedelta
from decimal import Decimal
import json

from .models import ChiusuraCassa, MovimentoCassa, Cassa, ChiusuraCassaAutomatica, QuadraturaGiornaliera, SpesaCassa
from .services import abbinamento_portali, allinea_scontrini, import_washtec

# Metodi che non finiscono nel conteggio fisico della quadratura
# (contanti scassettati + lettore carte): esclusi dal servito atteso.
METODI_NON_IN_CASSA = ('bonifico', 'assegno')
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
        'data_prev': data - timedelta(days=1),
        'data_next': data + timedelta(days=1),
        'oggi': timezone.now().date(),
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
            # Redirect preferibilmente al report giornata (flusso integrato)
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url == 'report':
                from django.urls import reverse
                return redirect(f"{reverse('finanze:report_giornata')}?data={data.strftime('%Y-%m-%d')}")
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
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url == 'report':
                from django.urls import reverse
                return redirect(f"{reverse('finanze:report_giornata')}?data={chiusura.data.strftime('%Y-%m-%d')}")
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
    # Forziamo la valutazione a lista per evitare re-query multiple.
    chiusure_auto = list(
        ChiusuraCassaAutomatica.objects.filter(data=data)
        .select_related('cassa')
        .order_by('cassa__ordine')
    )

    # Tutte le casse automatiche attive + relativa chiusura del giorno (per form inline)
    casse_attive = Cassa.objects.filter(tipo='automatica', attiva=True).order_by('ordine')
    _chiusure_by_cassa = {c.cassa_id: c for c in chiusure_auto}
    casse_form_data = [
        {'cassa': ca, 'chiusura': _chiusure_by_cassa.get(ca.pk)}
        for ca in casse_attive
    ]

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
    # le vendite di soli prodotti non sono ordini (gli incassi restano)
    num_ordini = ordini_giorno.filter(vendita_prodotti=False).count()
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
    contesto_portali = _contesto_lavaggi_portali(data)
    quadratura, spese_cassa, totale_spese_cassa = _quadratura_giornata(
        data, cassa_servito, agg['vendita_totale'], totale_servito,
        contesto_portali['abbinamento']['valore_residuo'])

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
        'data_prev': data - timedelta(days=1),
        'data_next': data + timedelta(days=1),
        'oggi': timezone.now().date(),
        'cassa_servito': cassa_servito,
        'chiusure_auto': chiusure_auto,
        'casse_form_data': casse_form_data,
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
    context.update(contesto_portali)
    from django.urls import reverse
    from apps.ordini.vendite import riepilogo_prodotti
    context['rp'] = riepilogo_prodotti(data, data)
    context['link_scheda_prodotti'] = f"{reverse('ordini:vendita-prodotti')}?data={data:%Y-%m-%d}"
    context.update({
        'spese_cassa': spese_cassa,
        'totale_spese_cassa': totale_spese_cassa,
        'categorie_spesa': SpesaCassa.CATEGORIA_CHOICES,
    })
    return render(request, 'finanze/report_giornata.html', context)


def _quadratura_giornata(data, cassa_servito, vendita_self_service, totale_servito,
                         residuo_operatori):
    """Quadratura giornaliera complessiva (scassettamento unificato).

    L'operatore scassetta TUTTE le casse automatiche + registratore,
    conta tutti i contanti insieme + lettore carte POS servito.
    Totale reale vs Totale teorico = vendita self-service + servito POS;
    la differenza sconta anche i lavaggi portale pagati direttamente agli
    operatori (residuo dell'abbinamento servito).
    Ritorna (quadratura, spese_cassa, totale_spese_cassa).
    """
    quadratura_obj = QuadraturaGiornaliera.objects.filter(data=data).first()

    def _per_ordine(pagamenti):
        """Pagamenti raggruppati per ordine: importo, metodi, riferimenti."""
        out = {}
        for p in pagamenti:
            voce = out.setdefault(p.ordine_id, {
                'ordine': p.ordine, 'importo': Decimal('0.00'),
                'metodi': [], 'riferimenti': [],
            })
            voce['importo'] += p.importo
            if p.get_metodo_display() not in voce['metodi']:
                voce['metodi'].append(p.get_metodo_display())
            if p.riferimento and p.riferimento not in voce['riferimenti']:
                voce['riferimenti'].append(p.riferimento)
        return list(out.values())

    # Bonifici e assegni non passano ne' dal cassetto ne' dal POS: esclusi
    # dal servito atteso e mostrati a parte. La chiusura cassa servito
    # somma gia' i bonifici (non gli assegni); senza chiusura il servito e'
    # la somma di tutti i pagamenti del giorno.
    pagamenti_giorno_qs = (Pagamento.objects.filter(data_pagamento__date=data)
                           .select_related('ordine__cliente')
                           .order_by('ordine__data_ora', 'data_pagamento'))
    non_in_cassa = _per_ordine(
        pagamenti_giorno_qs.filter(metodo__in=METODI_NON_IN_CASSA))
    totale_non_in_cassa = sum((v['importo'] for v in non_in_cassa), Decimal('0.00'))
    if cassa_servito:
        escluso_dal_servito = cassa_servito.totale_bonifici
    else:
        escluso_dal_servito = totale_non_in_cassa
    servito_atteso = totale_servito - escluso_dal_servito
    totale_teorico = vendita_self_service + servito_atteso

    # Il servito atteso comprende anche i crediti di giorni precedenti
    # riscossi oggi (in contanti/carta): li separo, per ordine.
    crediti_pregressi = _per_ordine(
        pagamenti_giorno_qs.filter(ordine__data_ora__date__lt=data)
        .exclude(metodo__in=METODI_NON_IN_CASSA))
    totale_crediti_pregressi = sum((c['importo'] for c in crediti_pregressi),
                                   Decimal('0.00'))
    servito_ordini_giorno = servito_atteso - totale_crediti_pregressi

    # Spese pagate coi contanti della cassa: mancano dal conteggio, quindi
    # si sommano al reale.
    spese_cassa = list(SpesaCassa.objects.filter(data=data).select_related('operatore'))
    totale_spese_cassa = sum((s.importo for s in spese_cassa), Decimal('0.00'))

    if quadratura_obj:
        fondo_cassa_iniziale = quadratura_obj.fondo_cassa_iniziale
        lordo_reale = quadratura_obj.contanti_totali + quadratura_obj.lettore_carte_servito
        totale_reale = quadratura_obj.totale_reale + totale_spese_cassa
        differenza_quadratura = totale_reale - totale_teorico
        if abs(differenza_quadratura) < Decimal('0.50'):
            stato_quadratura = 'ok'
        elif differenza_quadratura < 0:
            stato_quadratura = 'mancante'
        else:
            stato_quadratura = 'eccedente'
    else:
        fondo_cassa_iniziale = Decimal('0.00')
        lordo_reale = None
        totale_reale = None
        differenza_quadratura = None
        stato_quadratura = 'non_rilevato'

    quadratura = {
        'obj': quadratura_obj,
        'vendita_self_service': vendita_self_service,
        'totale_servito': servito_atteso,
        'servito_ordini_giorno': servito_ordini_giorno,
        'crediti_pregressi': crediti_pregressi,
        'totale_crediti_pregressi': totale_crediti_pregressi,
        'non_in_cassa': non_in_cassa,
        'totale_non_in_cassa': totale_non_in_cassa,
        'totale_teorico': totale_teorico,
        'fondo_cassa_iniziale': fondo_cassa_iniziale,
        'spese_cassa': totale_spese_cassa,
        'lordo_reale': lordo_reale,
        'totale_reale': totale_reale,
        'differenza': differenza_quadratura,
        'stato': stato_quadratura,
    }

    quadratura['residuo_operatori'] = residuo_operatori
    if quadratura['differenza'] is not None:
        quadratura['differenza_reale_teorico'] = quadratura['differenza']
        quadratura['differenza'] = quadratura['differenza'] - residuo_operatori
        if abs(quadratura['differenza']) < Decimal('0.50'):
            quadratura['stato'] = 'ok'
        elif quadratura['differenza'] < 0:
            quadratura['stato'] = 'mancante'
        else:
            quadratura['stato'] = 'eccedente'
    return quadratura, spese_cassa, totale_spese_cassa


def _chiusura_portali(data):
    """(chiusura, salvata) della giornata portali. Senza una chiusura
    salvata ritorna quella predefinita, non salvata: dalle 19:30 del
    giorno prima alle 19:30 del giorno, oppure dalla fine della chiusura
    del giorno prima se esiste (niente buchi ne' sovrapposizioni)."""
    from .models import ChiusuraPortali

    chiusura = ChiusuraPortali.objects.filter(data=data).first()
    if chiusura:
        return chiusura, True
    prec = ChiusuraPortali.objects.filter(data=data - timedelta(days=1)).first()
    alle_1930 = lambda g: timezone.make_aware(datetime.combine(g, time(19, 30)))
    da = prec.periodo_a if prec else alle_1930(data - timedelta(days=1))
    da_blu = prec.finestra('B')[1] if prec else da
    return ChiusuraPortali(
        data=data,
        periodo_da=da,
        periodo_a=alle_1930(data),
        periodo_da_blu=da_blu if da_blu != da else None,
    ), False


def _contesto_lavaggi_portali(data, completo=True):
    """Sezione 'Lavaggi portali (WashTec)' del report giornata.

    L'operatore imposta la finestra di chiusura (ChiusuraPortali);
    le tabelle per portale sono aggregate dall'archivio
    TransazionePortale: righe per programma con richieste/valore
    divisi per origine (contanti / unita' operativa). I programmi
    senza prezzo di listino vengono segnalati.
    """
    from .models import PREZZI_PROGRAMMA_PORTALE, TransazionePortale

    chiusura, salvata = _chiusura_portali(data)
    transazioni = list(TransazionePortale.objects.filter(chiusura.q_transazioni()))
    casse = allinea_scontrini.chiusure_casse_portali(data)
    scontrini = {p: (c.wash_cycles if c else None) for p, c in casse.items()}

    portali = []
    for codice, label in TransazionePortale.PORTALE_CHOICES:
        righe = []
        tot = {'richieste': 0, 'contanti': Decimal('0.00'),
               'unita': Decimal('0.00'), 'senza_prezzo': 0}
        for prog in range(1, 10):
            cella = {'programma': prog,
                     'prezzo': PREZZI_PROGRAMMA_PORTALE[prog]}
            for origine in ('contanti', 'unita'):
                n = sum(1 for t in transazioni
                        if t.portale == codice and t.programma == prog
                        and t.origine == origine)
                cella[f'n_{origine}'] = n
                cella[f'v_{origine}'] = PREZZI_PROGRAMMA_PORTALE[prog] * n
                tot['richieste'] += n
                tot[origine] += PREZZI_PROGRAMMA_PORTALE[prog] * n
                if n and not PREZZI_PROGRAMMA_PORTALE[prog]:
                    tot['senza_prezzo'] += n
            if cella['n_contanti'] or cella['n_unita']:
                righe.append(cella)
        portali.append({
            'codice': codice, 'label': label, 'righe': righe,
            'tot': tot, 'tot_valore': tot['contanti'] + tot['unita'],
            'finestra': chiusura.finestra(codice),
            'scontrino': scontrini[codice],
            'combacia': scontrini[codice] is None or scontrini[codice] == tot['richieste'],
            # i lavaggi pagati in contanti (a listino) devono fare la
            # vendita contante + non contante dello scontrino della cassa
            'vendita_scontrino': casse[codice].vendita_totale if casse[codice] else None,
        })
        portali[-1]['contanti_combaciano'] = (
            portali[-1]['vendita_scontrino'] is None
            or portali[-1]['vendita_scontrino'] == tot['contanti'])

    # Orari che fanno tornare i saldi (e i WashCycles) degli scontrini,
    # con l'inizio attaccato alla fine del giorno prima: proposti se
    # cambiano qualcosa o se qualche portale non torna
    allineamento, allineamento_cambia = None, False
    if completo and any(casse.values()):
        allineamento = allinea_scontrini.allinea(chiusura, casse)
        allineamento_cambia = any(
            v['fine'] and (v['fine'] != v['fine_attuale'] or v['da'] != v['da_attuale'])
            for v in allineamento.values())
        tornano = all(p['combacia'] and p['contanti_combaciano'] for p in portali)
        if tornano and not allineamento_cambia:
            allineamento = None

    # Lavaggi a cavallo di inizio e fine (+-20 min, al secondo): per
    # far combaciare la finestra con gli scontrini senza andare a tentativi
    margine = timedelta(minutes=20)
    bordi = []
    istanti = [('Inizio', chiusura.periodo_da), ('Fine', chiusura.periodo_a)]
    if chiusura.orari_blu_diversi:
        da_blu, a_blu = chiusura.finestra('B')
        istanti = [('Inizio Azzurro', chiusura.periodo_da), ('Fine Azzurro', chiusura.periodo_a)]
        istanti += [(e, i) for e, i in (('Inizio Blu', da_blu), ('Fine Blu', a_blu))
                    if i not in (chiusura.periodo_da, chiusura.periodo_a)]
    for etichetta, istante in (istanti if completo else []):
        vicine = (TransazionePortale.objects
                  .filter(orario__gte=istante - margine, orario__lte=istante + margine)
                  .order_by('orario', 'numero'))
        bordi.append({
            'etichetta': etichetta, 'istante': istante,
            'righe': [{'t': t, 'dentro': chiusura.contiene(t)} for t in vicine],
        })

    abbinamento = abbinamento_portali.riepilogo(chiusura)

    # WashCycles self service: lavaggi pagati in contanti al portale +
    # residuo unita' operativa (pagati direttamente agli operatori)
    n_contanti = sum(1 for t in transazioni if t.origine == 'contanti')
    v_contanti = sum((p['tot']['contanti'] for p in portali), Decimal('0.00'))
    washcycles_self = {
        'n': n_contanti + len(abbinamento['residuo']),
        'valore': v_contanti + abbinamento['valore_residuo'],
        'n_contanti': n_contanti,
        'n_operatori': len(abbinamento['residuo']),
    }

    return {
        'portali_chiusura': chiusura,
        'portali_chiusura_salvata': salvata,
        'lavaggi_portali': portali,
        'portali_n_transazioni': len(transazioni),
        'portali_bordi': bordi,
        'portali_buchi': import_washtec.buchi_archivio(
            min(chiusura.finestra('A')[0], chiusura.finestra('B')[0]),
            max(chiusura.finestra('A')[1], chiusura.finestra('B')[1])),
        'portali_allineamento': allineamento,
        'portali_allineamento_cambia': allineamento_cambia,
        'portali_archivio_totale': TransazionePortale.objects.count(),
        'abbinamento': abbinamento,
        'washcycles_self': washcycles_self,
    }


@login_required
@user_passes_test(is_staff_user)
def azione_abbinamento_portali(request):
    """POST dal report giornata: conferma le proposte, abbina a mano o
    rimuove un abbinamento servito <-> transazione portale."""
    from django.urls import reverse

    from .models import AbbinamentoPortale

    data_str = request.POST.get('data', '')
    torna = f"{reverse('finanze:report_giornata')}?data={data_str}#abbinamento-portali"
    if request.method != 'POST':
        return redirect('finanze:report_giornata')
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Data non valida.')
        return redirect('finanze:report_giornata')
    # Abbinare fissa la finestra: quella predefinita viene salvata
    chiusura, salvata = _chiusura_portali(data)
    if not salvata:
        chiusura.operatore = request.user
        chiusura.save()

    azione = request.POST.get('azione')
    if azione == 'conferma':
        n = abbinamento_portali.conferma_proposte(chiusura, request.user)
        messages.success(request, f'{n} abbinament{"o confermato" if n == 1 else "i confermati"}.')
    elif azione == 'abbina':
        try:
            item_id = int(request.POST.get('item_id'))
            tx_id = int(request.POST.get('transazione_id'))
        except (TypeError, ValueError):
            messages.error(request, 'Seleziona una transazione.')
            return redirect(torna)
        ok, msg = abbinamento_portali.abbina_manuale(
            chiusura, item_id, tx_id, request.user)
        (messages.success if ok else messages.error)(request, msg)
    elif azione == 'rimuovi':
        n, _ = AbbinamentoPortale.objects.filter(
            pk=request.POST.get('abbinamento_id'),
        ).filter(chiusura.q_transazioni('transazione__')).delete()
        if n:
            messages.success(request, 'Abbinamento rimosso.')
        else:
            messages.error(request, 'Abbinamento non trovato.')
    else:
        messages.error(request, 'Azione non valida.')
    return redirect(torna)


@login_required
@user_passes_test(is_staff_user)
def imposta_chiusura_portali(request):
    """POST form dal report giornata: salva la finestra di chiusura
    dei portali per la data; il report si riaggrega dall'archivio."""
    from django.urls import reverse

    from .models import ChiusuraPortali

    if request.method != 'POST':
        return redirect('finanze:report_giornata')
    data_str = request.POST.get('data', '')

    def _orario(campo, facoltativo=False):
        # datetime-local invia i secondi solo se diversi da :00
        testo = request.POST.get(campo, '').strip()
        if facoltativo and not testo:
            return None
        formato = '%Y-%m-%dT%H:%M:%S' if testo.count(':') == 2 else '%Y-%m-%dT%H:%M'
        return timezone.make_aware(datetime.strptime(testo, formato))

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
        periodo_da = _orario('periodo_da')
        periodo_a = _orario('periodo_a')
        # Orari del Blu: vuoti o uguali a quelli generali = nessuna differenza
        periodo_da_blu = _orario('periodo_da_blu', facoltativo=True)
        periodo_a_blu = _orario('periodo_a_blu', facoltativo=True)
    except ValueError:
        messages.error(request, 'Date della chiusura portali non valide.')
        return redirect(f"{reverse('finanze:report_giornata')}?data={data_str}")
    if periodo_da_blu == periodo_da:
        periodo_da_blu = None
    if periodo_a_blu == periodo_a:
        periodo_a_blu = None
    if periodo_a <= periodo_da or (periodo_a_blu or periodo_a) <= (periodo_da_blu or periodo_da):
        messages.error(request,
                       "La fine della chiusura deve essere dopo l'inizio.")
        return redirect(f"{reverse('finanze:report_giornata')}?data={data_str}")

    ChiusuraPortali.objects.update_or_create(
        data=data, defaults={'periodo_da': periodo_da,
                             'periodo_a': periodo_a,
                             'periodo_da_blu': periodo_da_blu,
                             'periodo_a_blu': periodo_a_blu,
                             'operatore': request.user})
    messages.success(request, 'Orari allineati agli scontrini.' if request.POST.get('allinea')
                     else 'Chiusura portali aggiornata.')
    return redirect(
        f"{reverse('finanze:report_giornata')}?data={data.strftime('%Y-%m-%d')}")


@login_required
@user_passes_test(is_staff_user)
def riepilogo_saldi_portali(request):
    """Riepilogo mensile dei saldi giornalieri: per ogni giorno la
    differenza della quadratura (reale - teorico - pagati agli operatori)
    e, come controllo, gli scontrini dei portali contro WashTec."""
    import calendar

    oggi = timezone.localdate()
    try:
        primo = datetime.strptime(request.GET.get('mese', ''), '%Y-%m').date()
    except ValueError:
        primo = oggi.replace(day=1)
    ultimo = primo.replace(day=calendar.monthrange(primo.year, primo.month)[1])
    precedente = (primo - timedelta(days=1)).replace(day=1)
    successivo = ultimo + timedelta(days=1)

    giorni = []
    tot = {'reale': Decimal('0.00'), 'teorico': Decimal('0.00'),
           'operatori': Decimal('0.00'), 'differenza': Decimal('0.00'),
           'spese': Decimal('0.00'), 'n_quadrature': 0, 'n_ok': 0,
           'n_portali_ok': 0, 'n_portali': 0}
    g = primo
    while g <= min(ultimo, oggi):
        quadratura, portali = _saldo_giornata(g)
        portali_scontrini = [p for p in portali['lavaggi_portali'] if p['scontrino'] is not None]
        riga = {
            'data': g, 'q': quadratura, 'portali': portali['lavaggi_portali'],
            'portali_tornano': all(p['combacia'] and p['contanti_combaciano']
                                   for p in portali['lavaggi_portali']),
            'portali_con_scontrini': bool(portali_scontrini),
            'washcycles_self': portali['washcycles_self'],
        }
        giorni.append(riga)
        if quadratura['differenza'] is not None:
            tot['n_quadrature'] += 1
            tot['n_ok'] += quadratura['stato'] == 'ok'
            tot['reale'] += quadratura['totale_reale']
            tot['teorico'] += quadratura['totale_teorico']
            tot['operatori'] += quadratura['residuo_operatori']
            tot['differenza'] += quadratura['differenza']
            tot['spese'] += quadratura['spese_cassa']
        if riga['portali_con_scontrini']:
            tot['n_portali'] += 1
            tot['n_portali_ok'] += riga['portali_tornano']
        g += timedelta(days=1)

    return render(request, 'finanze/riepilogo_saldi_portali.html', {
        'primo': primo, 'ultimo': min(ultimo, oggi),
        'precedente': precedente,
        'successivo': successivo if successivo <= oggi else None,
        'giorni': giorni, 'tot': tot,
    })


def _saldo_giornata(data):
    """(quadratura, contesto portali) di un giorno, con gli stessi calcoli
    del report giornata (versione leggera dei portali)."""
    cassa_servito = ChiusuraCassa.objects.filter(data=data).first()
    if cassa_servito:
        cassa_servito.ricalcola_totali()
    vendita_self_service = sum(
        (c.vendita_totale for c in ChiusuraCassaAutomatica.objects
         .filter(data=data, cassa__modalita_registratore=False)),
        Decimal('0.00'))
    if cassa_servito:
        totale_servito = cassa_servito.totale_incassi_giornalieri
    else:
        totale_servito = (Pagamento.objects.filter(data_pagamento__date=data)
                          .aggregate(s=Sum('importo'))['s'] or Decimal('0.00'))
    portali = _contesto_lavaggi_portali(data, completo=False)
    quadratura, _, _ = _quadratura_giornata(
        data, cassa_servito, vendita_self_service, totale_servito,
        portali['abbinamento']['valore_residuo'])
    return quadratura, portali


@login_required
@user_passes_test(is_staff_user)
def allinea_periodo_portali(request):
    """POST dal report giornata: allinea insieme le chiusure portali di
    piu' giorni agli scontrini (prima i saldi, poi i WashCycles)."""
    from django.urls import reverse

    torna = f"{reverse('finanze:report_giornata')}?data={request.POST.get('data', '')}"
    if request.POST.get('da_riepilogo'):
        torna = f"{reverse('finanze:riepilogo_saldi_portali')}?mese={request.POST.get('dal', '')[:7]}"
    if request.method != 'POST':
        return redirect('finanze:report_giornata')
    try:
        dal = datetime.strptime(request.POST.get('dal', ''), '%Y-%m-%d').date()
        al = datetime.strptime(request.POST.get('al', ''), '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Date del periodo non valide.')
        return redirect(torna)
    if al < dal or (al - dal).days > 62:
        messages.error(request, 'Periodo non valido (massimo due mesi).')
        return redirect(torna)

    esito = allinea_scontrini.salva_periodo(dal, al, request.user)
    tornano = saldi = con_scontrini = 0
    for voce in esito.values():
        vv = [v for v in voce.values() if v['vendita'] is not None]
        if not vv:
            continue
        con_scontrini += 1
        saldi += all(v['contanti'] == v['vendita'] for v in vv)
        tornano += all(v['conteggio'] == v['scontrino'] for v in vv)
    messages.success(request, (
        f"Chiusure portali dal {dal:%d/%m} al {al:%d/%m} allineate: su {con_scontrini} giorni "
        f"con scontrini, contanti esatti in {saldi} e WashCycles esatti in {tornano}."))
    return redirect(torna)


@login_required
@user_passes_test(is_staff_user)
def azione_spese_cassa(request):
    """POST dal report giornata: aggiunge o elimina una spesa pagata coi
    contanti della cassa. Elimina solo chi l'ha inserita o l'admin."""
    from django.urls import reverse

    data_str = request.POST.get('data', '')
    torna = f"{reverse('finanze:report_giornata')}?data={data_str}#spese-cassa"
    if request.method != 'POST':
        return redirect('finanze:report_giornata')

    azione = request.POST.get('azione')
    if azione == 'aggiungi':
        try:
            data = datetime.strptime(data_str, '%Y-%m-%d').date()
            importo = Decimal((request.POST.get('importo') or '').replace(',', '.'))
        except (ValueError, ArithmeticError):
            messages.error(request, 'Data o importo della spesa non validi.')
            return redirect(torna)
        descrizione = request.POST.get('descrizione', '').strip()
        categoria = request.POST.get('categoria', 'altro')
        if importo <= 0 or not descrizione:
            messages.error(request, 'Indica una descrizione e un importo maggiore di zero.')
            return redirect(torna)
        if categoria not in dict(SpesaCassa.CATEGORIA_CHOICES):
            categoria = 'altro'
        SpesaCassa.objects.create(
            data=data, importo=importo.quantize(Decimal('0.01')),
            descrizione=descrizione[:200], categoria=categoria,
            riferimento=request.POST.get('riferimento', '').strip()[:100],
            operatore=request.user)
        messages.success(request, f'Spesa "{descrizione}" di €{importo:.2f} registrata.')
    elif azione == 'elimina':
        spesa = get_object_or_404(SpesaCassa, pk=request.POST.get('spesa_id'))
        if not (request.user.is_superuser or spesa.operatore_id == request.user.id):
            messages.error(request, "Solo chi l'ha inserita o l'amministratore può eliminare la spesa.")
            return redirect(torna)
        spesa.delete()
        messages.success(request, 'Spesa eliminata.')
    return redirect(torna)


@login_required
@user_passes_test(is_staff_user)
def importa_transazioni_portali(request):
    """POST JSON: importa transazioni WashTec nell'archivio.

    Payload: {transazioni: [{portale, numero,
    orario 'YYYY-MM-DD HH:MM:SS', programma, origine}, ...]}.
    Idempotente: i duplicati (portale, numero) vengono ignorati.
    """
    from .models import TransazionePortale

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'errore': 'metodo non valido'},
                            status=405)
    try:
        payload = json.loads(request.body or '{}')
        voci = payload['transazioni']
        assert isinstance(voci, list)
    except (KeyError, ValueError, AssertionError, TypeError):
        return JsonResponse({'ok': False, 'errore': 'payload non valido'},
                            status=400)

    nuove = []
    for v in voci:
        try:
            orario = timezone.make_aware(datetime.strptime(
                v['orario'], '%Y-%m-%d %H:%M:%S'))
            prog = int(v['programma'])
            assert v['portale'] in ('A', 'B')
            assert v['origine'] in ('contanti', 'unita')
            assert 1 <= prog <= 9
            nuove.append(TransazionePortale(
                portale=v['portale'], numero=int(v['numero']),
                orario=orario, programma=prog, origine=v['origine']))
        except (KeyError, ValueError, AssertionError, TypeError):
            return JsonResponse({'ok': False,
                                 'errore': f'transazione non valida: {v}'},
                                status=400)

    TransazionePortale.objects.bulk_create(nuove, ignore_conflicts=True)
    return JsonResponse({'ok': True, 'ricevute': len(nuove),
                         'totale_archivio': TransazionePortale.objects.count()})


@login_required
@user_passes_test(is_staff_user)
def importa_washtec(request):
    """Pagina di arrivo del bookmarklet WashTec: legge le righe dal
    fragment dell'URL (#...) lato browser, mostra l'anteprima e importa.
    Contiene anche il bookmarklet da trascinare nei preferiti."""
    from .models import TransazionePortale

    adesso = timezone.now()
    ultima = TransazionePortale.objects.order_by('-orario').first()
    return render(request, 'finanze/importa_washtec.html', {
        'origine_gestionale': request.build_absolute_uri('/').rstrip('/'),
        'ultima_transazione': ultima,
        'buchi': import_washtec.buchi_archivio(adesso - timedelta(days=60), adesso),
    })


@login_required
@user_passes_test(is_staff_user)
def importa_washtec_api(request):
    """POST JSON {righe: [[numero, orario, programma, metodo, pagato,
    manutenzione, eseguito], ...], conferma: bool}: classifica le righe
    grezze WashTec e, con conferma, le salva in archivio."""
    from .services import import_washtec

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'errore': 'metodo non valido'},
                            status=405)
    try:
        payload = json.loads(request.body or '{}')
        righe = payload['righe']
        assert isinstance(righe, list)
    except (KeyError, ValueError, AssertionError, TypeError):
        return JsonResponse({'ok': False, 'errore': 'payload non valido'},
                            status=400)
    esito = import_washtec.importa(righe, conferma=bool(payload.get('conferma')))
    return JsonResponse({'ok': True, **esito})


@login_required
@user_passes_test(is_staff_user)
def quadratura_form(request):
    """Form per inserire/modificare la quadratura giornaliera complessiva."""
    data = _parse_data(request)
    quadratura = QuadraturaGiornaliera.objects.filter(data=data).first()

    if request.method == 'POST':
        try:
            contanti = Decimal(request.POST.get('contanti_totali') or '0')
            lettore = Decimal(request.POST.get('lettore_carte_servito') or '0')
            fondo = Decimal(request.POST.get('fondo_cassa_iniziale') or '0')
            note = request.POST.get('note', '')
            QuadraturaGiornaliera.objects.update_or_create(
                data=data,
                defaults={
                    'contanti_totali': contanti,
                    'lettore_carte_servito': lettore,
                    'fondo_cassa_iniziale': fondo,
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
        'data_prev': data - timedelta(days=1),
        'data_next': data + timedelta(days=1),
        'oggi': timezone.now().date(),
        'quadratura': quadratura,
    })


# ---------------------------------------------------------------------------
# Report periodo (aggregato multi-giorno)
# ---------------------------------------------------------------------------

def _compute_periodo(preset, data_inizio_str, data_fine_str, oggi):
    """Calcola (data_inizio, data_fine, preset_attivo) in base ai parametri."""
    if preset == 'oggi':
        return oggi, oggi, 'oggi'
    if preset == 'ieri':
        ieri = oggi - timedelta(days=1)
        return ieri, ieri, 'ieri'
    if preset == '7gg':
        return oggi - timedelta(days=6), oggi, '7gg'
    if preset == '30gg':
        return oggi - timedelta(days=29), oggi, '30gg'
    if preset == 'mese_corrente':
        return oggi.replace(day=1), oggi, 'mese_corrente'
    if preset == 'mese_scorso':
        primo_mese = oggi.replace(day=1)
        ultimo_scorso = primo_mese - timedelta(days=1)
        primo_scorso = ultimo_scorso.replace(day=1)
        return primo_scorso, ultimo_scorso, 'mese_scorso'
    if preset == 'anno_corrente':
        return oggi.replace(month=1, day=1), oggi, 'anno_corrente'
    # personalizzato o default
    if data_inizio_str and data_fine_str:
        try:
            di = datetime.strptime(data_inizio_str, '%Y-%m-%d').date()
            df = datetime.strptime(data_fine_str, '%Y-%m-%d').date()
            if df < di:
                di, df = df, di
            return di, df, 'personalizzato'
        except ValueError:
            pass
    # Default: ultimi 7 giorni
    return oggi - timedelta(days=6), oggi, '7gg'


@login_required
@user_passes_test(is_staff_user)
def report_periodo(request):
    """Report aggregato su un range di date con KPI business-oriented."""
    oggi = timezone.now().date()
    preset = request.GET.get('preset')
    di_str = request.GET.get('data_inizio')
    df_str = request.GET.get('data_fine')
    data_inizio, data_fine, preset_attivo = _compute_periodo(preset, di_str, df_str, oggi)

    giorni_periodo = (data_fine - data_inizio).days + 1

    # ==================== AGGREGATI ORDINI ====================
    from apps.ordini.vendite import riepilogo_prodotti
    ordini_qs = Ordine.objects.filter(
        data_ora__date__gte=data_inizio,
        data_ora__date__lte=data_fine,
    ).exclude(stato='annullato')
    # le vendite di soli prodotti non sono ordini: fuori dal conteggio e
    # dallo scontrino medio, ma dentro il fatturato servito
    num_ordini = ordini_qs.filter(vendita_prodotti=False).count()
    totale_servito_ordinato = ordini_qs.aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00')
    totale_ordini_lavaggio = (ordini_qs.filter(vendita_prodotti=False)
                              .aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00'))

    # Non pagati
    non_pagati_qs = ordini_qs.filter(stato_pagamento__in=['non_pagato', 'parziale', 'differito'])
    num_non_pagati = non_pagati_qs.count()
    totale_non_pagato = Decimal('0.00')
    for o in non_pagati_qs:
        totale_non_pagato += o.saldo_dovuto

    # ==================== AGGREGATI PAGAMENTI ====================
    pagamenti_qs = Pagamento.objects.filter(
        data_pagamento__date__gte=data_inizio,
        data_pagamento__date__lte=data_fine,
    )
    totale_servito_pagato = pagamenti_qs.aggregate(s=Sum('importo'))['s'] or Decimal('0.00')
    num_transazioni = pagamenti_qs.count()

    # Metodi pagamento
    metodi_totali = {}
    for p in pagamenti_qs.values('metodo').annotate(tot=Sum('importo')):
        metodi_totali[p['metodo'] or 'altro'] = p['tot'] or Decimal('0.00')

    metodi_labels = {
        'contanti': 'Contanti', 'carta': 'Carta', 'bancomat': 'Bancomat',
        'bonifico': 'Bonifico', 'assegno': 'Assegno', 'abbonamento': 'Abbonamento',
        'altro': 'Altro',
    }
    metodi_chart = sorted(
        [(metodi_labels.get(k, k.title()), float(v)) for k, v in metodi_totali.items()],
        key=lambda x: -x[1]
    )

    # ==================== AGGREGATI CHIUSURE AUTOMATICHE ====================
    chiusure_qs = ChiusuraCassaAutomatica.objects.filter(
        data__gte=data_inizio, data__lte=data_fine,
    ).select_related('cassa')

    totale_portali = Decimal('0.00')
    totale_cambia_gettoni = Decimal('0.00')
    totale_registratore = Decimal('0.00')
    totale_wash_cycles = 0
    vendita_self_service = Decimal('0.00')

    for c in chiusure_qs:
        if c.cassa.modalita_registratore:
            totale_registratore += c.incasso_totale
            continue
        vendita_self_service += c.vendita_totale
        if c.cassa.tracking_washcycles:
            totale_portali += c.vendita_totale
            if c.wash_cycles:
                totale_wash_cycles += c.wash_cycles
        else:
            totale_cambia_gettoni += c.vendita_totale

    totale_self_service = totale_portali + totale_cambia_gettoni

    # ==================== TOTALI E KPI ====================
    fatturato_totale = totale_servito_ordinato + totale_self_service
    fatturato_medio_giornaliero = fatturato_totale / giorni_periodo if giorni_periodo > 0 else Decimal('0.00')
    # Scontrino medio = fatturato servito ordinato / numero ordini
    # (era: (servito_pagato + self_service) / num_transazioni di Pagamento
    # che includeva nel numeratore vendite self-service ma divideva per
    # i soli Pagamento POS = valore inflato e fuorviante).
    scontrino_medio = totale_ordini_lavaggio / num_ordini if num_ordini > 0 else Decimal('0.00')

    # ==================== RILEVATO REALE (quadrature giornaliere) ====================
    # Somma delle Quadrature giornaliere nel periodo. Usa la stessa
    # formula del report giornata: contanti + lettore carte - fondo cassa.
    quadrature_qs = QuadraturaGiornaliera.objects.filter(
        data__gte=data_inizio, data__lte=data_fine,
    )
    totale_rilevato_reale = Decimal('0.00')
    num_quadrature = 0
    for q in quadrature_qs:
        totale_rilevato_reale += q.totale_reale
        num_quadrature += 1
    # Differenza vs teorico (= fatturato_totale incassato)
    diff_rilevato_vs_teorico = totale_rilevato_reale - fatturato_totale

    # Corrispettivi fiscali
    totale_corrispettivi = totale_registratore + vendita_self_service
    IVA_RATE = Decimal('0.22')
    if totale_corrispettivi > 0:
        imponibile_corrispettivi = (totale_corrispettivi / (Decimal('1') + IVA_RATE)).quantize(Decimal('0.01'))
        iva_corrispettivi = totale_corrispettivi - imponibile_corrispettivi
    else:
        imponibile_corrispettivi = Decimal('0.00')
        iva_corrispettivi = Decimal('0.00')

    # % incasso
    totale_incassato = totale_servito_pagato + totale_self_service
    pct_incasso = float(totale_incassato / fatturato_totale * 100) if fatturato_totale > 0 else 100.0

    # ==================== CONFRONTO PERIODO PRECEDENTE ====================
    data_inizio_prev = data_inizio - timedelta(days=giorni_periodo)
    data_fine_prev = data_inizio - timedelta(days=1)
    fatturato_prev_ordini = Ordine.objects.filter(
        data_ora__date__gte=data_inizio_prev,
        data_ora__date__lte=data_fine_prev,
    ).exclude(stato='annullato').aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00')
    vendita_prev_auto = Decimal('0.00')
    for c in ChiusuraCassaAutomatica.objects.filter(
        data__gte=data_inizio_prev, data__lte=data_fine_prev,
    ).select_related('cassa'):
        if not c.cassa.modalita_registratore:
            vendita_prev_auto += c.vendita_totale
    fatturato_prev = fatturato_prev_ordini + vendita_prev_auto
    variazione_pct = float((fatturato_totale - fatturato_prev) / fatturato_prev * 100) if fatturato_prev > 0 else None

    # ==================== QUADRATURA AGGREGATA ====================
    quadrature_qs = QuadraturaGiornaliera.objects.filter(
        data__gte=data_inizio, data__lte=data_fine,
    )
    giorni_ok = 0
    giorni_diff = 0
    giorni_rilevati = 0
    differenza_cumulativa = Decimal('0.00')
    giorno_peggiore = None
    peggiore_abs = Decimal('0.00')

    # Indice pre-calcolato: teorico per giorno
    for q in quadrature_qs:
        # Ricalcola teorico per quel giorno
        day_ordini = Ordine.objects.filter(
            data_ora__date=q.data
        ).exclude(stato='annullato').aggregate(s=Sum('totale_finale'))['s'] or Decimal('0.00')
        day_pag = Pagamento.objects.filter(
            data_pagamento__date=q.data
        ).aggregate(s=Sum('importo'))['s'] or Decimal('0.00')
        day_self = Decimal('0.00')
        for c in ChiusuraCassaAutomatica.objects.filter(data=q.data).select_related('cassa'):
            if not c.cassa.modalita_registratore:
                day_self += c.vendita_totale
        day_teorico = day_self + day_pag
        day_diff = q.totale_reale - day_teorico
        differenza_cumulativa += day_diff
        giorni_rilevati += 1
        if abs(day_diff) < Decimal('0.50'):
            giorni_ok += 1
        else:
            giorni_diff += 1
            if abs(day_diff) > peggiore_abs:
                peggiore_abs = abs(day_diff)
                giorno_peggiore = {'data': q.data, 'differenza': day_diff}
    giorni_non_rilevati = giorni_periodo - giorni_rilevati

    # ==================== TOP SERVIZI E CATEGORIE ====================
    items_qs = ItemOrdine.objects.filter(
        ordine__data_ora__date__gte=data_inizio,
        ordine__data_ora__date__lte=data_fine,
        ordine__stato_pagamento='pagato',
    ).select_related('servizio_prodotto__categoria')

    servizi_stats = {}
    categorie_stats = {}
    for item in items_qs:
        sp = item.servizio_prodotto
        servizi_stats.setdefault(sp.titolo, {
            'nome': sp.titolo,
            'categoria': sp.categoria.nome if sp.categoria else '—',
            'quantita': 0, 'fatturato': Decimal('0.00'),
        })
        servizi_stats[sp.titolo]['quantita'] += item.quantita
        servizi_stats[sp.titolo]['fatturato'] += item.subtotale
        if sp.categoria:
            categorie_stats.setdefault(sp.categoria.nome, {
                'nome': sp.categoria.nome, 'quantita': 0, 'fatturato': Decimal('0.00'),
            })
            categorie_stats[sp.categoria.nome]['quantita'] += item.quantita
            categorie_stats[sp.categoria.nome]['fatturato'] += item.subtotale

    top_servizi = sorted(servizi_stats.values(), key=lambda x: -x['fatturato'])[:10]
    top_categorie = sorted(categorie_stats.values(), key=lambda x: -x['fatturato'])
    categorie_chart = [
        {'nome': c['nome'], 'fatturato': float(c['fatturato']), 'quantita': c['quantita']}
        for c in top_categorie
    ]

    # ==================== TREND GIORNALIERO ====================
    trend_labels = []
    trend_servito = []
    trend_portali = []
    trend_cambia = []
    trend_washcycles = []

    # Pre-indicizza per data
    ord_by_day = {}
    for row in ordini_qs.values('data_ora__date').annotate(s=Sum('totale_finale')):
        ord_by_day[row['data_ora__date']] = row['s'] or Decimal('0.00')

    auto_by_day = {}  # {date: {'portali': X, 'cambia': Y, 'washcycles': Z}}
    for c in chiusure_qs:
        if c.cassa.modalita_registratore:
            continue
        d = c.data
        auto_by_day.setdefault(d, {'portali': Decimal('0.00'), 'cambia': Decimal('0.00'), 'washcycles': 0})
        if c.cassa.tracking_washcycles:
            auto_by_day[d]['portali'] += c.vendita_totale
            if c.wash_cycles:
                auto_by_day[d]['washcycles'] += c.wash_cycles
        else:
            auto_by_day[d]['cambia'] += c.vendita_totale

    d = data_inizio
    while d <= data_fine:
        trend_labels.append(d.strftime('%d/%m'))
        trend_servito.append(float(ord_by_day.get(d, Decimal('0.00'))))
        a = auto_by_day.get(d, {'portali': Decimal('0.00'), 'cambia': Decimal('0.00'), 'washcycles': 0})
        trend_portali.append(float(a['portali']))
        trend_cambia.append(float(a['cambia']))
        trend_washcycles.append(a['washcycles'])
        d += timedelta(days=1)

    # ==================== WASHCYCLES: AGGREGATO / SERVITO / SELF ====================
    # Aggregato = totale wash_cycles erogati dai portali (trend_washcycles)
    # Servito   = quantita totale di item 'lavaggio completo' venduti dal
    #             servito (sono i washcycles attivati dall'operatore per
    #             clienti che hanno ordinato un lavaggio completo)
    # Self      = aggregato - servito (clienti che hanno usato il portale
    #             autonomamente con token/abbonamento)
    # ItemOrdine e' gia importato a top file (riga 12)
    servito_by_day = {}
    servito_items_qs = (
        ItemOrdine.objects.filter(
            ordine__data_ora__date__gte=data_inizio,
            ordine__data_ora__date__lte=data_fine,
            servizio_prodotto__titolo__icontains='lavaggio completo',
        )
        .exclude(ordine__stato='annullato')
        .values('ordine__data_ora__date')
        .annotate(qty=Sum('quantita'))
    )
    for row in servito_items_qs:
        d = row['ordine__data_ora__date']
        servito_by_day[d] = int(row['qty'] or 0)

    trend_wc_aggregato = list(trend_washcycles)  # alias semantico
    trend_wc_servito = []
    trend_wc_self = []
    d = data_inizio
    while d <= data_fine:
        agg = servito_by_day.get(d, 0)
        trend_wc_servito.append(agg)
        d += timedelta(days=1)
    for i in range(len(trend_wc_aggregato)):
        trend_wc_self.append(max(0, trend_wc_aggregato[i] - trend_wc_servito[i]))

    totale_wc_aggregato = sum(trend_wc_aggregato)
    totale_wc_servito = sum(trend_wc_servito)
    totale_wc_self = sum(trend_wc_self)

    # Per giorno della settimana (0=lun ... 6=dom)
    gs_wc_aggregato = [0] * 7
    gs_wc_servito = [0] * 7
    gs_wc_self = [0] * 7
    for i in range(len(trend_labels)):
        d_date = data_inizio + timedelta(days=i)
        wd = d_date.weekday()
        gs_wc_aggregato[wd] += trend_wc_aggregato[i]
        gs_wc_servito[wd] += trend_wc_servito[i]
        gs_wc_self[wd] += trend_wc_self[i]

    # ==================== METEO LICATA + LAG ANALYSIS ====================
    # Tenta di recuperare dati meteo Open-Meteo. Best-effort: se l'API
    # non risponde la sezione meteo nel template viene nascosta.
    from .weather import fetch_weather_range, correlate_revenue_weather
    from .lag_analysis import lag_correlation_series
    weather = fetch_weather_range(data_inizio, data_fine)
    fatturato_per_giorno = [
        trend_servito[i] + trend_portali[i] + trend_cambia[i]
        for i in range(len(trend_labels))
    ]
    weather_corr = correlate_revenue_weather(fatturato_per_giorno, weather) if weather else {}

    # Allinea le serie meteo all'array trend_labels (giorno per giorno).
    # Se weather ha buchi, riempi con null per Chart.js.
    weather_temp_max = []
    weather_precip = []
    weather_sun = []
    if weather:
        wmap_tmax = dict(zip(weather['dates'], weather['temp_max']))
        wmap_pr = dict(zip(weather['dates'], weather['precipitation']))
        wmap_sun = dict(zip(weather['dates'], weather['sunshine_hours']))
        d = data_inizio
        while d <= data_fine:
            weather_temp_max.append(wmap_tmax.get(d))
            weather_precip.append(wmap_pr.get(d))
            weather_sun.append(wmap_sun.get(d))
            d += timedelta(days=1)

    # Lag analysis meteo: correlazione fatturato di oggi vs meteo di N
    # giorni fa. Lag 0..7.
    weather_lag = {}
    if weather:
        lags = list(range(0, 8))
        weather_lag = {
            'lags': lags,
            'temp_max': lag_correlation_series(weather_temp_max, fatturato_per_giorno, lags),
            'precipitation': lag_correlation_series(weather_precip, fatturato_per_giorno, lags),
            'sunshine_hours': lag_correlation_series(weather_sun, fatturato_per_giorno, lags),
        }

    # ==================== CAMBIA GETTONI (piste + accessori) ====================
    # Fatturato per giorno settimana
    gs_cambia = [0.0] * 7
    for i in range(len(trend_labels)):
        d_date = data_inizio + timedelta(days=i)
        gs_cambia[d_date.weekday()] += trend_cambia[i]

    media_cambia_giornaliera = (
        float(totale_cambia_gettoni) / giorni_periodo if giorni_periodo > 0 else 0.0
    )
    # Picco settimana (giorno con piu cambia)
    if any(gs_cambia):
        wd_picco_idx = gs_cambia.index(max(gs_cambia))
        wd_picco_label = ['Lunedi', 'Martedi', 'Mercoledi', 'Giovedi', 'Venerdi', 'Sabato', 'Domenica'][wd_picco_idx]
        wd_picco_val = gs_cambia[wd_picco_idx]
    else:
        wd_picco_label = '-'
        wd_picco_val = 0.0

    # ==================== FATTURATO PER GIORNO SETTIMANA ====================
    # 0=lun ... 6=dom
    giorni_settimana_labels = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
    giorni_settimana_tot = [0.0] * 7
    for i, label in enumerate(trend_labels):
        d_date = data_inizio + timedelta(days=i)
        giorni_settimana_tot[d_date.weekday()] += trend_servito[i] + trend_portali[i] + trend_cambia[i]

    # Giorno di picco
    max_idx = trend_servito and max(range(len(trend_servito)),
        key=lambda i: trend_servito[i] + trend_portali[i] + trend_cambia[i])
    giorno_picco = None
    if trend_servito:
        pk_date = data_inizio + timedelta(days=max_idx)
        pk_tot = trend_servito[max_idx] + trend_portali[max_idx] + trend_cambia[max_idx]
        if pk_tot > 0:
            giorno_picco = {'data': pk_date, 'totale': pk_tot}

    # ==================== DISTRIBUZIONE ORARIA MEDIA ====================
    # Usa l'ora locale (timezone Europe/Rome), non UTC come e' nel DB
    ore_buckets = [0.0] * 24
    for p in pagamenti_qs:
        dt = p.data_pagamento
        local_hour = timezone.localtime(dt).hour if timezone.is_aware(dt) else dt.hour
        ore_buckets[local_hour] += float(p.importo)
    ora_picco = None
    if any(ore_buckets):
        h = ore_buckets.index(max(ore_buckets))
        ora_picco = f"{h:02d}:00"

    washcycles_per_giorno = totale_wash_cycles / giorni_periodo if giorni_periodo > 0 else 0

    context = {
        'data_inizio': data_inizio,
        'data_fine': data_fine,
        'giorni_periodo': giorni_periodo,
        'preset_attivo': preset_attivo,
        # Hero
        'fatturato_totale': fatturato_totale,
        'fatturato_medio_giornaliero': fatturato_medio_giornaliero,
        'num_ordini': num_ordini,
        'scontrino_medio': scontrino_medio,
        'rp': riepilogo_prodotti(data_inizio, data_fine),
        'variazione_pct': variazione_pct,
        'fatturato_prev': fatturato_prev,
        # Canali
        'totale_servito_ordinato': totale_servito_ordinato,
        'totale_servito_pagato': totale_servito_pagato,
        'totale_portali': totale_portali,
        'totale_cambia_gettoni': totale_cambia_gettoni,
        'totale_registratore': totale_registratore,
        'totale_self_service': totale_self_service,
        # Fiscali
        'totale_corrispettivi': totale_corrispettivi,
        'iva_corrispettivi': iva_corrispettivi,
        'imponibile_corrispettivi': imponibile_corrispettivi,
        # Rilevato reale (quadrature giornaliere)
        'totale_rilevato_reale': totale_rilevato_reale,
        'num_quadrature': num_quadrature,
        'diff_rilevato_vs_teorico': diff_rilevato_vs_teorico,
        'giorni_senza_quadratura': giorni_periodo - num_quadrature,
        # Volumi
        'totale_wash_cycles': totale_wash_cycles,
        'washcycles_per_giorno': washcycles_per_giorno,
        'giorno_picco': giorno_picco,
        'ora_picco': ora_picco or '—',
        # Crediti
        'totale_non_pagato': totale_non_pagato,
        'num_non_pagati': num_non_pagati,
        'pct_incasso': pct_incasso,
        # Quadratura
        'giorni_ok': giorni_ok,
        'giorni_diff': giorni_diff,
        'giorni_non_rilevati': giorni_non_rilevati,
        'differenza_cumulativa': differenza_cumulativa,
        'giorno_peggiore': giorno_peggiore,
        # Top
        'top_servizi': top_servizi,
        'top_categorie': top_categorie,
        # Chart data JSON
        'trend_labels_json': json.dumps(trend_labels),
        'trend_servito_json': json.dumps(trend_servito),
        'trend_portali_json': json.dumps(trend_portali),
        'trend_cambia_json': json.dumps(trend_cambia),
        'trend_washcycles_json': json.dumps(trend_washcycles),
        # Washcycles aggregato/servito/self
        'totale_wc_aggregato': totale_wc_aggregato,
        'totale_wc_servito': totale_wc_servito,
        'totale_wc_self': totale_wc_self,
        'trend_wc_aggregato_json': json.dumps(trend_wc_aggregato),
        'trend_wc_servito_json': json.dumps(trend_wc_servito),
        'trend_wc_self_json': json.dumps(trend_wc_self),
        'gs_wc_aggregato_json': json.dumps(gs_wc_aggregato),
        'gs_wc_servito_json': json.dumps(gs_wc_servito),
        'gs_wc_self_json': json.dumps(gs_wc_self),
        # Cambia gettoni (piste + accessori)
        'media_cambia_giornaliera': media_cambia_giornaliera,
        'wd_picco_cambia_label': wd_picco_label,
        'wd_picco_cambia_val': wd_picco_val,
        'gs_cambia_json': json.dumps(gs_cambia),
        # Meteo + correlazione fatturato
        'weather_available': bool(weather),
        'weather_corr': weather_corr,
        'weather_temp_max_json': json.dumps(weather_temp_max),
        'weather_precip_json': json.dumps(weather_precip),
        'weather_sun_json': json.dumps(weather_sun),
        'fatturato_per_giorno_json': json.dumps(fatturato_per_giorno),
        # Lag analysis meteo
        'weather_lag': weather_lag,
        'weather_lag_json': json.dumps({
            'lags': weather_lag.get('lags', []),
            'temp_max': [x['corr'] for x in weather_lag.get('temp_max', [])],
            'precipitation': [x['corr'] for x in weather_lag.get('precipitation', [])],
            'sunshine_hours': [x['corr'] for x in weather_lag.get('sunshine_hours', [])],
        }) if weather_lag else 'null',
        'giorni_settimana_labels_json': json.dumps(giorni_settimana_labels),
        'giorni_settimana_tot_json': json.dumps(giorni_settimana_tot),
        'ore_buckets_json': json.dumps(ore_buckets),
        'metodi_chart_json': json.dumps(metodi_chart),
        'categorie_chart_json': json.dumps(categorie_chart),
    }
    return render(request, 'finanze/report_periodo.html', context)
