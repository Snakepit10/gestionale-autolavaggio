"""Contabilita' analitica (Amministrazione > Contabilita'): direct costing
del self-service, full costing del servito, margine dei prodotti e
impostazioni delle ripartizioni."""
from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from .analitica import analitica as calcola_analitica
from .conto_economico import BASI_RICAVI, NOMI_MESI
from .models import CENTRI, DRIVER_COMUNI, Cespite, ParametriAnalitica, RipartizionePersonale, VoceConto
from .views import _decimale, _mese, accesso, campi_ripartizione, ripartizione_da_post

SEZIONI_SENZA_DRIVER = ('ricavi', 'non_economico', 'oneri_finanziari', 'imposte')


def driver_consigliato(nome):
    """Proposta di driver dal nome della voce o del cespite."""
    n = nome.lower()
    if any(k in n for k in ('manutenz', 'chimic', 'acqua', 'portal', 'impiant', 'spazzol')):
        return 'cicli'
    if any(k in n for k in ('software', 'gestional', 'divis', 'formazion', 'personale')):
        return 'ore'
    return 'ricavi'


def voci_e_cespiti_comuni():
    """Voci di costo senza ripartizione (i loro costi restano sul Comune,
    salvo righe ripartite a mano) e cespiti del centro Comune."""
    voci = [v for v in VoceConto.objects.filter(attiva=True).exclude(sezione__in=SEZIONI_SENZA_DRIVER)
            if not v.ripartizione]
    cespiti = list(Cespite.objects.filter(centro='comune'))
    for x in voci + cespiti:
        x.consigliato = driver_consigliato(str(x))
    return voci, cespiti


def _periodo(request):
    """Anno, mese (o tutto l'anno) e base dei ricavi dai parametri GET,
    comuni a tutte le schede dell'analitica."""
    oggi = timezone.localdate()
    try:
        anno = int(request.GET.get('anno', oggi.year))
    except ValueError:
        anno = oggi.year
    mese = request.GET.get('mese', '')
    if mese.isdigit() and 1 <= int(mese) <= 12:
        mesi = [date(anno, int(mese), 1)]
        periodo = f'{NOMI_MESI[int(mese) - 1]} {anno}'
    else:
        mese = ''
        mesi = [date(anno, m, 1) for m in range(1, 13)]
        periodo = str(anno)
    # ricavi come nel conto economico: di default il rilevato reale meno IVA
    base = request.GET.get('ricavi', 'rilevato')
    if base not in BASI_RICAVI:
        base = 'rilevato'
    return mesi, {
        'base': base,
        'basi': BASI_RICAVI.items(),
        'anno': anno,
        'mese': mese,
        'periodo': periodo,
        'mesi_nomi': list(enumerate(NOMI_MESI, 1)),
    }


@accesso
def analitica(request):
    mesi, contesto = _periodo(request)
    return render(request, 'contabilita/analitica.html', {
        'a': calcola_analitica(mesi, contesto['base']), 'scheda': 'centri', **contesto})


@accesso
def produttivita(request):
    from .produttivita import produttivita as calcola
    mesi, contesto = _periodo(request)
    return render(request, 'contabilita/analitica_produttivita.html', {
        'p': calcola(mesi, contesto['anno'], contesto['base']), 'scheda': 'produttivita', **contesto})


@accesso
def clienti_abbonamenti(request):
    from .clienti_abbonamenti import clienti_e_abbonamenti
    mesi, contesto = _periodo(request)
    return render(request, 'contabilita/analitica_clienti.html', {
        'c': clienti_e_abbonamenti(mesi, contesto['base']), 'scheda': 'clienti', **contesto})


@accesso
def budget(request):
    from .budget import GRANDEZZE, budget_e_scostamenti
    from .models import BudgetServito
    mesi, contesto = _periodo(request)
    anno = contesto['anno']
    if request.method == 'POST':
        errori, salvati = [], 0
        for n in range(1, 13):
            mese = date(anno, n, 1)
            valori = {}
            for campo in GRANDEZZE:
                testo = (request.POST.get(f'b_{n}_{campo}') or '').strip()
                if not testo:
                    valori[campo] = None
                    continue
                numero = _decimale(testo)
                if numero is None or numero < 0 or (campo == 'lavaggi' and numero != numero.to_integral_value()):
                    errori.append(f'{NOMI_MESI[n - 1]}: valore non valido "{testo}"')
                    valori = None
                    break
                valori[campo] = int(numero) if campo == 'lavaggi' else numero
            if valori is None:
                continue
            if any(v is not None for v in valori.values()):
                BudgetServito.objects.update_or_create(mese=mese, defaults=valori)
                salvati += 1
            else:
                BudgetServito.objects.filter(mese=mese).delete()
        for e in errori:
            messages.error(request, e)
        messages.success(request, f'Budget {anno} salvato ({salvati} mesi con valori propri).')
        return redirect(f"{request.path}?anno={anno}&mese={contesto['mese']}&ricavi={contesto['base']}")
    return render(request, 'contabilita/analitica_budget.html', {
        'b': budget_e_scostamenti(mesi, anno, contesto['base']), 'scheda': 'budget', **contesto})


@accesso
def impostazioni_analitica(request):
    from apps.personale.models import Dipendente
    if request.method == 'POST':
        p = request.POST
        if p.get('azione') == 'personale':
            dal = _mese(p.get('dal'), timezone.localdate().replace(day=1))
            salvati, errori = 0, []
            for d in Dipendente.objects.filter(attivo=True):
                try:
                    rip = ripartizione_da_post(p, f'rip_{d.pk}')
                except ValueError as e:
                    errori.append(f'{d}: {e}')
                    continue
                attuale = d.ripartizioni_centri.filter(valido_dal__lte=dal).order_by('-valido_dal').first()
                if rip and (not attuale or attuale.ripartizione != rip):
                    RipartizionePersonale.objects.update_or_create(dipendente=d, valido_dal=dal, defaults={'ripartizione': rip})
                    salvati += 1
            for e in errori:
                messages.error(request, e)
            if salvati:
                messages.success(request, f'Ripartizione aggiornata per {salvati} dipendenti dal {dal:%m/%Y}.')
        elif p.get('azione') == 'driver':
            validi, cambiati = dict(DRIVER_COMUNI), 0
            voci, cespiti = voci_e_cespiti_comuni()
            for prefisso, oggetti in (('drv_v', voci), ('drv_c', cespiti)):
                for x in oggetti:
                    scelto = p.get(f'{prefisso}_{x.pk}')
                    if scelto in validi and scelto != x.driver_comuni:
                        x.driver_comuni = scelto
                        x.save(update_fields=['driver_comuni'])
                        cambiati += 1
            messages.success(request, f'Criteri dei costi comuni salvati ({cambiati} cambiati).')
        elif p.get('azione') == 'minuti':
            from apps.core.models import ServizioProdotto
            cambiati, errori = 0, []
            for s in ServizioProdotto.objects.filter(tipo='servizio', attivo=True):
                testo = (p.get(f'min_{s.pk}') or '').strip()
                if not testo:
                    valore = None
                elif testo.isdigit() and 0 < int(testo) <= 600:
                    valore = int(testo)
                else:
                    errori.append(s.titolo)
                    continue
                if valore != s.minuti_persona_standard:
                    s.minuti_persona_standard = valore
                    s.save(update_fields=['minuti_persona_standard'])
                    cambiati += 1
            if errori:
                messages.error(request, 'Minuti non validi (da 1 a 600): ' + ', '.join(errori))
            messages.success(request, f'Minuti standard salvati ({cambiati} cambiati).')
        elif p.get('azione') == 'obiettivo':
            valore = _decimale(p.get('margine_obiettivo'))
            if valore is None or not 0 <= valore < 100:
                messages.error(request, 'Margine obiettivo non valido (da 0 a 99).')
            else:
                parametri = ParametriAnalitica.attuali()
                parametri.margine_obiettivo = valore
                parametri.save()
                messages.success(request, f'Margine obiettivo: {valore}%.')
        return redirect('contabilita:impostazioni_analitica')

    oggi = timezone.localdate().replace(day=1)
    dipendenti = list(Dipendente.objects.filter(attivo=True))
    for d in dipendenti:
        attuale = d.ripartizioni_centri.filter(valido_dal__lte=oggi).order_by('-valido_dal').first()
        d.campi_rip = campi_ripartizione(attuale.ripartizione if attuale else {}, {'servito': 100})
        d.storico = list(d.ripartizioni_centri.all())
        d.prefisso_rip = f'rip_{d.pk}'
    voci, cespiti = voci_e_cespiti_comuni()
    from apps.core.models import ServizioProdotto

    from .capacita import minuti_rilevati
    rilevati = minuti_rilevati()
    servizi = list(ServizioProdotto.objects.filter(tipo='servizio', attivo=True).order_by('categoria__nome', 'titolo'))
    for s in servizi:
        s.rilevati = rilevati.get(s.pk)
    return render(request, 'contabilita/impostazioni_analitica.html', {
        'servizi': servizi,
        'parametri': ParametriAnalitica.attuali(),
        'dipendenti': dipendenti,
        'centri': CENTRI,
        'voci_comuni': voci,
        'cespiti_comuni': cespiti,
        'driver': DRIVER_COMUNI,
        'oggi': oggi,
    })

