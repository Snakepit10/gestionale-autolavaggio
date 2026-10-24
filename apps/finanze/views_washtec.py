"""Archivio delle transazioni dei portali estratte da WashTec."""
import csv
from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import PREZZI_PROGRAMMA_PORTALE, TransazionePortale
from .services.import_washtec import buchi_archivio
from .views import is_staff_user

STATI = [
    ('', 'Tutte'),
    ('abbinata', 'Abbinate a un ordine'),
    ('libera', 'Unità operativa non abbinate'),
    ('rettificata', 'Rettificate (omaggio, promo)'),
    ('interrotta', 'Interrotte (non contano)'),
]


def _data(valore, default):
    try:
        return datetime.strptime(valore, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


@login_required
@user_passes_test(is_staff_user)
def transazioni_washtec(request):
    oggi = timezone.localdate()
    dal = _data(request.GET.get('dal'), oggi)
    al = max(_data(request.GET.get('al'), dal), dal)
    inizio = timezone.make_aware(datetime.combine(dal, time.min))
    fine = timezone.make_aware(datetime.combine(al + timedelta(days=1), time.min))

    qs = (TransazionePortale.tutte.filter(orario__gte=inizio, orario__lt=fine)
          .select_related('abbinamento__item__ordine__cliente', 'abbinamento__item__servizio_prodotto',
                          'rettifica_residuo')
          .order_by('orario', 'portale', 'numero'))
    filtro = {k: request.GET.get(k, '') for k in ('portale', 'origine', 'programma', 'stato', 'numero')}
    if filtro['portale'] in ('A', 'B'):
        qs = qs.filter(portale=filtro['portale'])
    if filtro['origine'] in ('contanti', 'unita'):
        qs = qs.filter(origine=filtro['origine'])
    if filtro['programma'].isdigit():
        qs = qs.filter(programma=int(filtro['programma']))
    if filtro['numero'].isdigit():
        qs = qs.filter(numero=int(filtro['numero']))
    if filtro['stato'] == 'abbinata':
        qs = qs.filter(abbinamento__isnull=False)
    elif filtro['stato'] == 'libera':
        qs = qs.filter(origine='unita', abbinamento__isnull=True, eseguita=True)
    elif filtro['stato'] == 'rettificata':
        qs = qs.filter(rettifica_residuo__isnull=False)
    elif filtro['stato'] == 'interrotta':
        qs = qs.filter(eseguita=False)

    if request.GET.get('formato') == 'csv':
        return _csv(qs, dal, al)

    # riepilogo sull'intero filtro (non solo la pagina)
    # i lavaggi interrotti si elencano ma non contano
    righe = list(qs.filter(eseguita=True).values_list('portale', 'origine', 'programma'))
    per_origine = Counter((p, o) for p, o, _ in righe)
    valore = {o: sum((PREZZI_PROGRAMMA_PORTALE.get(pr, Decimal('0')) for _, oo, pr in righe if oo == o),
                     Decimal('0')) for o in ('contanti', 'unita')}
    programmi = sorted(Counter(pr for _, _, pr in righe).items())

    pagina = Paginator(qs, 200).get_page(request.GET.get('page'))
    for t in pagina.object_list:
        t.listino = PREZZI_PROGRAMMA_PORTALE.get(t.programma, Decimal('0'))
    query = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'formato') and v)
    ultima = TransazionePortale.objects.order_by('-orario').first()
    return render(request, 'finanze/transazioni_washtec.html', {
        'dal': dal, 'al': al, 'oggi': oggi, 'filtro': filtro, 'stati': STATI,
        'portali': TransazionePortale.PORTALE_CHOICES, 'origini': TransazionePortale.ORIGINE_CHOICES,
        'pagina': pagina, 'query': query,
        'totale': len(righe),
        'n_interrotte': qs.filter(eseguita=False).count(),
        'riepilogo': [{'portale': nome, 'contanti': per_origine[(codice, 'contanti')],
                       'unita': per_origine[(codice, 'unita')]}
                      for codice, nome in TransazionePortale.PORTALE_CHOICES],
        'valore_contanti': valore['contanti'], 'valore_unita': valore['unita'],
        'programmi': programmi,
        'buchi': buchi_archivio(inizio, fine),
        'ultima': ultima,
        'giorno_prec': dal - timedelta(days=1), 'giorno_succ': dal + timedelta(days=1),
    })


def _csv(qs, dal, al):
    risposta = HttpResponse(content_type='text/csv; charset=utf-8')
    risposta['Content-Disposition'] = f'attachment; filename="washtec_{dal:%Y%m%d}_{al:%Y%m%d}.csv"'
    risposta.write('﻿')                         # Excel legge l'UTF-8
    w = csv.writer(risposta, delimiter=';')
    w.writerow(['Data', 'Ora', 'Portale', 'Numero', 'Programma', 'Origine', 'Listino', 'Ordine abbinato',
                'Servizio', 'Rettifica', 'Incassato', 'Eseguito'])
    for t in qs:
        abb = getattr(t, 'abbinamento', None) if hasattr(t, 'abbinamento') else None
        rett = getattr(t, 'rettifica_residuo', None) if hasattr(t, 'rettifica_residuo') else None
        locale = timezone.localtime(t.orario)
        w.writerow([
            f'{locale:%d/%m/%Y}', f'{locale:%H:%M:%S}', t.get_portale_display(), t.numero, f'P{t.programma}',
            t.get_origine_display(), str(PREZZI_PROGRAMMA_PORTALE.get(t.programma, '')).replace('.', ','),
            abb.item.ordine.numero_progressivo if abb else '',
            abb.item.servizio_prodotto.titolo if abb else '',
            rett.get_motivo_display() if rett else '',
            str(rett.importo).replace('.', ',') if rett else '',
            'Sì' if t.eseguita else 'No (interrotto)',
        ])
    return risposta
