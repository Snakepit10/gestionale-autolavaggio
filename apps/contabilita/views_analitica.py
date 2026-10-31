"""Contabilita' analitica (Amministrazione > Contabilita'): direct costing
del self-service, full costing del servito, margine dei prodotti e
impostazioni delle ripartizioni."""
from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from .analitica import analitica as calcola_analitica
from .conto_economico import NOMI_MESI
from .models import CENTRI, ParametriAnalitica, RipartizionePersonale
from .views import _decimale, _mese, accesso, campi_ripartizione, ripartizione_da_post


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
    return render(request, 'contabilita/analitica.html', {
        'a': calcola_analitica(mesi),
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
        elif p.get('azione') == 'parametri':
            parametri = ParametriAnalitica.attuali()
            pct = _decimale(p.get('percentuale_servito'))
            if p.get('criterio_comuni') not in dict(ParametriAnalitica.CRITERI) or pct is None or not 0 <= pct <= 100:
                messages.error(request, 'Parametri non validi.')
            else:
                parametri.criterio_comuni, parametri.percentuale_servito = p['criterio_comuni'], pct
                parametri.save()
                messages.success(request, 'Criterio dei costi comuni salvato.')
        return redirect('contabilita:impostazioni_analitica')

    oggi = timezone.localdate().replace(day=1)
    dipendenti = list(Dipendente.objects.filter(attivo=True))
    for d in dipendenti:
        attuale = d.ripartizioni_centri.filter(valido_dal__lte=oggi).order_by('-valido_dal').first()
        d.campi_rip = campi_ripartizione(attuale.ripartizione if attuale else {}, {'servito': 100})
        d.storico = list(d.ripartizioni_centri.all())
        d.prefisso_rip = f'rip_{d.pk}'
    return render(request, 'contabilita/impostazioni_analitica.html', {
        'dipendenti': dipendenti,
        'centri': CENTRI,
        'parametri': ParametriAnalitica.attuali(),
        'criteri': ParametriAnalitica.CRITERI,
        'oggi': oggi,
    })

