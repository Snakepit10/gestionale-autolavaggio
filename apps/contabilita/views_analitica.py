"""Contabilita' analitica (Amministrazione > Contabilita'): direct costing
del self-service, full costing del servito, margine dei prodotti e
impostazioni delle ripartizioni."""
from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from .analitica import analitica as calcola_analitica
from .conto_economico import BASI_RICAVI, NOMI_MESI
from .models import CENTRI, DRIVER_COMUNI, Cespite, RipartizionePersonale, VoceConto
from .views import _mese, accesso, campi_ripartizione, ripartizione_da_post

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


@accesso
def analitica(request):
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
    return render(request, 'contabilita/analitica.html', {
        'a': calcola_analitica(mesi, base),
        'base': base,
        'basi': BASI_RICAVI.items(),
        'anno': anno,
        'mese': mese,
        'periodo': periodo,
        'mesi_nomi': list(enumerate(NOMI_MESI, 1)),
    })


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
        return redirect('contabilita:impostazioni_analitica')

    oggi = timezone.localdate().replace(day=1)
    dipendenti = list(Dipendente.objects.filter(attivo=True))
    for d in dipendenti:
        attuale = d.ripartizioni_centri.filter(valido_dal__lte=oggi).order_by('-valido_dal').first()
        d.campi_rip = campi_ripartizione(attuale.ripartizione if attuale else {}, {'servito': 100})
        d.storico = list(d.ripartizioni_centri.all())
        d.prefisso_rip = f'rip_{d.pk}'
    voci, cespiti = voci_e_cespiti_comuni()
    return render(request, 'contabilita/impostazioni_analitica.html', {
        'dipendenti': dipendenti,
        'centri': CENTRI,
        'voci_comuni': voci,
        'cespiti_comuni': cespiti,
        'driver': DRIVER_COMUNI,
        'oggi': oggi,
    })

