"""Chiusura giornata degli operatori (dalla lista ordini).

Gli operatori contano i contanti e il POS e dichiarano ticket di resto
pagati, promo e omaggi, spese pagate dalla cassa e lavaggi extra fatti
senza ordine. L'incrocio con la vendita lo fa il report giornata, dove il
titolare conferma la giornata: da quel momento gli operatori non
modificano piu' nulla.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.auth_system.sezioni import ha_accesso

from .models import (Cassa, LavaggioExtra, PromoGiornata, QuadraturaGiornaliera, RicaricaCassa, SpesaCassa,
                     TicketResto)

# azione -> modello delle voci che si aggiungono/eliminano
VOCI = {
    'ricarica': RicaricaCassa,
    'ticket': TicketResto,
    'spesa': SpesaCassa,
    'extra': LavaggioExtra,
    'promo': PromoGiornata,
}


def _data(request):
    oggi = timezone.localdate()
    try:
        giorno = datetime.strptime(request.POST.get('data') or request.GET.get('data') or '', '%Y-%m-%d').date()
    except ValueError:
        return oggi
    return min(giorno, oggi)


def _importo(valore):
    try:
        importo = Decimal(str(valore or '').replace(',', '.').strip())
    except InvalidOperation:
        return None
    return importo.quantize(Decimal('0.01')) if importo > 0 else None


def casse_ricaricabili():
    """Casse automatiche self service (non il registratore)."""
    return Cassa.objects.filter(tipo='automatica', attiva=True, modalita_registratore=False).order_by('ordine')


def giornata_confermata(giorno):
    return QuadraturaGiornaliera.objects.filter(data=giorno, confermata_il__isnull=False).exists()


def puo_modificare(user, giorno):
    """Gli operatori fino alla conferma, chi ha le Finanze sempre."""
    return ha_accesso(user, 'finanze') or not giornata_confermata(giorno)


@login_required
def chiusura_giornata(request):
    giorno = _data(request)
    torna = f"{reverse('ordini:chiusura-giornata')}?data={giorno:%Y-%m-%d}"
    if request.method == 'POST':
        if not puo_modificare(request.user, giorno):
            messages.error(request, 'La giornata è già stata confermata: chiedi al titolare di sbloccarla.')
            return redirect(torna)
        _azione(request, giorno)
        return redirect(torna + (request.POST.get('ancora') or ''))

    quadratura = QuadraturaGiornaliera.objects.filter(data=giorno).select_related('confermata_da').first()
    voci = {nome: list(modello.objects.filter(data=giorno).select_related(
                *(['operatore', 'cassa'] if nome == 'ricarica' else ['operatore'])))
            for nome, modello in VOCI.items()}
    totali = {nome: sum((v.importo for v in lista), Decimal('0.00')) for nome, lista in voci.items()}
    oggi = timezone.localdate()
    return render(request, 'ordini/chiusura_giornata.html', {
        'data': giorno,
        'oggi': oggi,
        'data_prev': giorno - timedelta(days=1),
        'data_next': giorno + timedelta(days=1) if giorno < oggi else None,
        'quadratura': quadratura,
        'confermata': bool(quadratura and quadratura.confermata_il),
        'modificabile': puo_modificare(request.user, giorno),
        'voci': voci,
        'totali': totali,
        'promo_macchine': sum((p.importo for p in voci['promo'] if p.tipo == 'macchine'), Decimal('0.00')),
        'promo_operatori': sum((p.importo for p in voci['promo'] if p.tipo == 'operatori'), Decimal('0.00')),
        'categorie_spesa': SpesaCassa.CATEGORIA_CHOICES,
        'casse_ricarica': casse_ricaricabili(),
        'tipi_promo': PromoGiornata.TIPO_CHOICES,
        'metodi_extra': LavaggioExtra.METODO_CHOICES,
        'vede_report': ha_accesso(request.user, 'finanze'),
    })


def _azione(request, giorno):
    p = request.POST
    azione = p.get('azione', '')
    if azione == 'conteggio':
        valori = {}
        for campo in ('contanti_totali', 'lettore_carte_servito', 'fondo_cassa_iniziale'):
            testo = (p.get(campo) or '0').replace(',', '.').strip() or '0'
            try:
                valori[campo] = Decimal(testo).quantize(Decimal('0.01'))
            except InvalidOperation:
                messages.error(request, 'Importi del conteggio non validi.')
                return
            if valori[campo] < 0:
                messages.error(request, 'Gli importi non possono essere negativi.')
                return
        QuadraturaGiornaliera.objects.update_or_create(
            data=giorno, defaults={**valori, 'note': p.get('note', '').strip(), 'operatore': request.user})
        messages.success(request, 'Conteggio salvato.')
        return

    if azione.startswith('elimina_'):
        modello = VOCI.get(azione[len('elimina_'):])
        voce = modello.objects.filter(pk=p.get('id'), data=giorno).first() if modello else None
        if voce is None:
            messages.error(request, 'Voce non trovata.')
        elif voce.operatore_id != request.user.pk and not ha_accesso(request.user, 'finanze'):
            messages.error(request, "Solo chi l'ha inserita o il titolare può eliminarla.")
        else:
            voce.delete()
            messages.success(request, 'Voce eliminata.')
        return

    importo = _importo(p.get('importo'))
    if importo is None:
        messages.error(request, "Indica un importo maggiore di zero.")
        return
    descrizione = (p.get('descrizione') or '').strip()
    if azione == 'aggiungi_ricarica':
        cassa = casse_ricaricabili().filter(pk=p.get('cassa') or None).first()
        if cassa is None:
            messages.error(request, 'Scegli la cassa ricaricata.')
            return
        RicaricaCassa.objects.create(data=giorno, cassa=cassa, importo=importo,
                                     nota=(p.get('nota') or '').strip()[:100], operatore=request.user)
        messages.success(request, f'Ricarica di €{importo} su {cassa} registrata.')
    elif azione == 'aggiungi_ticket':
        TicketResto.objects.create(data=giorno, importo=importo, nota=(p.get('nota') or '').strip()[:100],
                                   operatore=request.user)
        messages.success(request, f'Ticket di resto di €{importo} registrato.')
    elif azione == 'aggiungi_spesa':
        if not descrizione:
            messages.error(request, 'Scrivi cosa hai pagato.')
            return
        categoria = p.get('categoria') if p.get('categoria') in dict(SpesaCassa.CATEGORIA_CHOICES) else 'altro'
        SpesaCassa.objects.create(data=giorno, importo=importo, descrizione=descrizione[:200], categoria=categoria,
                                  riferimento=(p.get('riferimento') or '').strip()[:100], operatore=request.user)
        messages.success(request, f'Spesa "{descrizione}" di €{importo} registrata.')
    elif azione == 'aggiungi_extra':
        if not descrizione:
            messages.error(request, 'Scrivi che lavaggio è stato fatto.')
            return
        metodo = p.get('metodo') if p.get('metodo') in dict(LavaggioExtra.METODO_CHOICES) else 'contanti'
        LavaggioExtra.objects.create(data=giorno, descrizione=descrizione[:150], importo=importo, metodo=metodo,
                                     operatore=request.user)
        messages.success(request, f'Lavaggio extra "{descrizione}" di €{importo} registrato.')
    elif azione == 'aggiungi_promo':
        tipo = p.get('tipo')
        if tipo not in dict(PromoGiornata.TIPO_CHOICES) or not descrizione:
            messages.error(request, 'Scegli il tipo di promo e scrivi una descrizione.')
            return
        PromoGiornata.objects.create(data=giorno, tipo=tipo, descrizione=descrizione[:150], importo=importo,
                                     operatore=request.user)
        messages.success(request, f'Promo "{descrizione}" di €{importo} registrata.')
    else:
        messages.error(request, 'Azione non valida.')


# Tipi della distinta cartacea, nell'ordine delle caselle
TIPI_DISTINTA = ['Resto', 'Ricarica', 'Omaggio', 'Promo', 'Spese', 'Extra']
RIGHE_DISTINTA = 22


@login_required
def distinta_chiusura(request):
    """Distinta cartacea della chiusura giornata (A4 fronte/retro): sul
    fronte i movimenti (gia' registrati + righe vuote), sul retro il
    conteggio della cassa. ?vuota=1 per un modulo in bianco."""
    giorno = _data(request)
    vuota = request.GET.get('vuota') == '1'
    quadratura = QuadraturaGiornaliera.objects.filter(data=giorno).first()
    righe = []
    if not vuota:
        for r in RicaricaCassa.objects.filter(data=giorno).select_related('cassa'):
            righe.append(('Ricarica', f'{r.cassa.nome}{" - " + r.nota if r.nota else ""}', r.importo))
        for t in TicketResto.objects.filter(data=giorno):
            righe.append(('Resto', t.nota or 'Ticket di resto', t.importo))
        for p in PromoGiornata.objects.filter(data=giorno):
            righe.append(('Promo', f'{p.descrizione} ({"macchine" if p.tipo == "macchine" else "operatori"})',
                          p.importo))
        for s in SpesaCassa.objects.filter(data=giorno):
            righe.append(('Spese', s.descrizione + (f' (n. {s.riferimento})' if s.riferimento else ''), s.importo))
        for x in LavaggioExtra.objects.filter(data=giorno):
            righe.append(('Extra', f'{x.descrizione} ({x.get_metodo_display().lower()})', x.importo))
    righe = [{'tipo': t, 'descrizione': d, 'importo': i} for t, d, i in righe]
    righe += [None] * max(RIGHE_DISTINTA - len(righe), 6)
    return render(request, 'ordini/distinta_chiusura.html', {
        'data': None if vuota else giorno,
        'quadratura': None if vuota else quadratura,
        'righe': righe,
        'tipi': TIPI_DISTINTA,
    })
