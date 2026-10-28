"""Distinta di chiusura giornata (scheda "Distinta" della sezione Ordini).

Gli operatori riportano i movimenti della prima pagina della distinta:
ricariche delle casse, ticket di resto pagati, spese pagate dalla cassa,
promo e omaggi, lavaggi extra fatti senza ordine. Il conteggio della
cassa (contanti, POS, fondo) lo fa solo chi ha il permesso "Conteggio
cassa" (Configurazione > Permessi). Il titolare fa l'incrocio con la
vendita nel report giornata e conferma la giornata: da quel momento gli
operatori non modificano piu' nulla.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.auth_system.sezioni import ha_accesso

from .models import (Cassa, ChiusuraCassaAutomatica, LavaggioExtra, PromoGiornata, QuadraturaGiornaliera, RicaricaCassa, SpesaCassa,
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


def fa_conteggio(user):
    """Vede e salva il conteggio della cassa (e atteso/saldo stampati)."""
    return ha_accesso(user, 'conteggio_cassa')


def fa_scontrini(user):
    """Inserisce gli scontrini di chiusura delle casse automatiche."""
    return ha_accesso(user, 'scontrini_casse')


def _scontrini_casse(giorno, ricariche):
    """Casse automatiche attive con lo scontrino del giorno (se c'e') e
    il totale delle ricariche dichiarate dagli operatori."""
    chiusure = {c.cassa_id: c for c in ChiusuraCassaAutomatica.objects.filter(data=giorno)}
    return [{'cassa': c, 'chiusura': chiusure.get(c.pk),
             'ricarica_dichiarata': sum((r.importo for r in ricariche if r.cassa_id == c.pk), Decimal('0.00'))}
            for c in Cassa.objects.filter(tipo='automatica', attiva=True).order_by('ordine')]


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
    from .services import import_automatico
    from .views import _contesto_lavaggi_portali, estratto_pagamenti
    from .views_spazzole import fa_spazzole
    # Abbinamento spazzole <-> servito: lo stesso riquadro del report giornata
    abbinamento = (_contesto_lavaggi_portali(giorno, completo=False, con_proposte=request.GET.get('proposte') == '1')
                   ['abbinamento'] if fa_spazzole(request.user) else None)
    return render(request, 'ordini/chiusura_giornata.html', {
        'abbinamento': abbinamento,
        'estratto': estratto_pagamenti(giorno),
        'stato_washtec': import_automatico.stato() if abbinamento else None,
        'url_abbinamento': reverse('ordini:chiusura-abbinamento'),
        'data': giorno,
        'oggi': oggi,
        'data_prev': giorno - timedelta(days=1),
        'data_next': giorno + timedelta(days=1) if giorno < oggi else None,
        'quadratura': quadratura,
        'confermata': bool(quadratura and quadratura.confermata_il),
        'modificabile': puo_modificare(request.user, giorno),
        'voci': voci,
        'totali': totali,
        'n_movimenti': sum(len(lista) for lista in voci.values()),
        'promo_macchine': sum((p.importo for p in voci['promo'] if p.tipo == 'macchine'), Decimal('0.00')),
        'promo_operatori': sum((p.importo for p in voci['promo'] if p.tipo == 'operatori'), Decimal('0.00')),
        'categorie_spesa': SpesaCassa.CATEGORIA_CHOICES,
        'casse_ricarica': casse_ricaricabili(),
        'tipi_promo': PromoGiornata.TIPO_CHOICES,
        'metodi_extra': LavaggioExtra.METODO_CHOICES,
        'vede_report': ha_accesso(request.user, 'finanze'),
        'fa_conteggio': fa_conteggio(request.user),
        'fa_scontrini': fa_scontrini(request.user),
        'scontrini': _scontrini_casse(giorno, voci['ricarica']) if fa_scontrini(request.user) else [],
        'scontrini_salvati': ChiusuraCassaAutomatica.objects.filter(
            data=giorno, cassa__tipo='automatica', cassa__attiva=True).count(),
    })


def _salva_scontrino(request, giorno):
    """Crea o aggiorna lo scontrino di chiusura di una cassa automatica
    (stessi campi del report giornata). Vuoto = 0; il registratore ha
    solo il totale. Uno scontrino confermato non si modifica."""
    p = request.POST
    cassa = Cassa.objects.filter(tipo='automatica', attiva=True, pk=p.get('cassa') or None).first()
    if cassa is None:
        messages.error(request, 'Cassa non trovata.')
        return
    campi = ['incasso_totale'] if cassa.modalita_registratore else [
        'incasso_totale', 'incasso_ricarica', 'vendita_contante', 'vendita_non_contante', 'resto_erogato_reale']
    valori = {}
    for campo in campi:
        try:
            valori[campo] = Decimal((p.get(campo) or '0').replace(',', '.').strip() or '0').quantize(Decimal('0.01'))
        except InvalidOperation:
            valori[campo] = None
        if valori[campo] is None or valori[campo] < 0:
            messages.error(request, f'{cassa}: importi non validi.')
            return
    if cassa.tracking_washcycles:
        testo = (p.get('wash_cycles') or '').strip()
        if testo and not testo.isdigit():
            messages.error(request, f'{cassa}: WashCycles non validi.')
            return
        valori['wash_cycles'] = int(testo) if testo else None
    chiusura = ChiusuraCassaAutomatica.objects.filter(cassa=cassa, data=giorno).first()
    if chiusura and chiusura.confermata:
        messages.error(request, f'Lo scontrino di {cassa} è confermato: non si modifica.')
        return
    if chiusura is None:
        chiusura = ChiusuraCassaAutomatica(cassa=cassa, data=giorno, operatore=request.user)
    for campo, valore in valori.items():
        setattr(chiusura, campo, valore)
    chiusura.save()
    messages.success(request, f'Scontrino di {cassa} salvato.')


def _azione(request, giorno):
    p = request.POST
    azione = p.get('azione', '')
    if azione == 'scontrino':
        if not fa_scontrini(request.user):
            messages.error(request, 'Non hai il permesso per inserire gli scontrini delle casse.')
            return
        _salva_scontrino(request, giorno)
        return

    if azione == 'conteggio':
        if not fa_conteggio(request.user):
            messages.error(request, 'Non hai il permesso per il conteggio della cassa.')
            return
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


# Righe minime di ogni riquadro dei movimenti della distinta cartacea
RIGHE_DISTINTA = 8
# Tagli del conteggio dei contanti sul retro
TAGLI_BANCONOTE = ['100', '50', '20', '10', '5']
TAGLI_MONETE = ['2', '1', '0,50', '0,20', '0,10', '0,05']


def _con_righe_vuote(voci, minimo=RIGHE_DISTINTA):
    return list(voci) + [None] * max(minimo - len(voci), 0)


@login_required
def distinta_chiusura(request):
    """Distinta cartacea della chiusura giornata (A4 fronte/retro).
    Fronte: i movimenti della cassa in un riquadro per tipo (ricariche,
    ticket di resto, spese, promo, lavaggi extra), con le voci gia'
    registrate e righe vuote. Retro: conteggio dei contanti per taglio e
    totale reale (operatore), poi atteso, residuo portali e saldo finale
    (ufficio), con gli stessi passaggi della quadratura del report
    giornata. ?vuota=1 per un modulo in bianco. Atteso e saldo si stampano
    gia' calcolati solo per chi ha il permesso "Conteggio cassa"."""
    giorno = _data(request)
    vuota = request.GET.get('vuota') == '1'
    quadratura = None if vuota else QuadraturaGiornaliera.objects.filter(data=giorno).first()
    voci = {nome: ([] if vuota else list(modello.objects.filter(data=giorno).select_related(
                *(['cassa'] if nome == 'ricarica' else []))))
            for nome, modello in VOCI.items()}

    def totale(lista, filtro=lambda v: True):
        scelte = [v.importo for v in lista if filtro(v)]
        return sum(scelte, Decimal('0.00')) if scelte else None

    saldo = None if vuota else _saldo_distinta(giorno, quadratura, fa_conteggio(request.user))
    return render(request, 'ordini/distinta_chiusura.html', {
        'data': None if vuota else giorno,
        'quadratura': quadratura,
        'ricariche': _con_righe_vuote(voci['ricarica']),
        'ticket': _con_righe_vuote(voci['ticket']),
        'spese': _con_righe_vuote(voci['spesa']),
        'promo': _con_righe_vuote(voci['promo']),
        'extra': _con_righe_vuote(voci['extra']),
        'totali': {
            'ricarica': totale(voci['ricarica']),
            'ticket': totale(voci['ticket']),
            'spesa': totale(voci['spesa']),
            'promo_macchine': totale(voci['promo'], lambda p: p.tipo == 'macchine'),
            'promo_operatori': totale(voci['promo'], lambda p: p.tipo == 'operatori'),
            'extra': totale(voci['extra']),
        },
        'tagli_banconote': TAGLI_BANCONOTE,
        'tagli_monete': TAGLI_MONETE,
        'saldo': saldo,
    })


def _saldo_distinta(giorno, quadratura, con_atteso):
    """Righe del retro della distinta (stessi conti della quadratura del
    report giornata): i valori noti sono gia' compilati, gli altri restano
    vuoti da scrivere a mano. Atteso e saldo solo se con_atteso."""
    from .models import ChiusuraCassaAutomatica
    from .views import _saldo_giornata

    q, _ = _saldo_giornata(giorno)
    contato = quadratura is not None
    saldo = {
        'contanti': quadratura.contanti_totali if contato else None,
        'pos': quadratura.lettore_carte_servito if contato else None,
        'fondo': quadratura.fondo_cassa_iniziale if contato else None,
        'spese': q['spese_cassa'] if q['spese_cassa'] else None,
        'ticket': q['ticket_resto'] if q['ticket_resto'] else None,
        'reale': q['totale_reale'],
    }
    if not con_atteso:
        return saldo
    scontrini = list(ChiusuraCassaAutomatica.objects.filter(data=giorno, cassa__modalita_registratore=False))
    if scontrini:
        saldo['incasso_casse'] = sum((c.incasso_totale for c in scontrini), Decimal('0.00'))
        saldo['ricarica_casse'] = sum((c.incasso_ricarica for c in scontrini), Decimal('0.00'))
    saldo.update({
        'promo_macchine': q['promo_macchine'],
        'servito': q['totale_servito'],
        'extra': q['totale_extra'],
        'atteso': q['totale_teorico'],
        'residuo_lordo': q['residuo_operatori_lordo'],
        'promo_operatori': q['promo_operatori'],
        'operatori': q['residuo_operatori'],
        'saldo': q['differenza'],
        'stato': q['stato'],
        'con_atteso': True,
    })
    return saldo
