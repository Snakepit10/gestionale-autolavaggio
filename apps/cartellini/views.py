"""Views per il modulo Cartellini Kanban.

Tutto staff-only. Un'unica pagina HTML (generatore) + un piccolo API JSON
per CRUD dei set salvati. La logica del generatore (CSS/JS) vive nel
template ed e' identica al file standalone, eccetto la barra "Set salvati"
che chiama questi endpoint via fetch().
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import render, get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods

from .models import SetCartellini


def _staff_required(view_func):
    """Decorator: login + is_staff. 302 -> login se anonimo, 403 se non staff."""
    @login_required
    def _wrap(request, *args, **kwargs):
        if not request.user.is_staff:
            return JsonResponse({'error': 'forbidden'}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrap


@_staff_required
def generatore(request):
    """Pagina principale: editor cartellini."""
    return render(request, 'cartellini/generatore.html')


def _serialize(s: SetCartellini, with_config: bool = False) -> dict:
    data = {
        'id': s.id,
        'nome': s.nome,
        'descrizione': s.descrizione,
        'num_cartellini': s.num_cartellini,
        'creato_da': s.creato_da.username if s.creato_da else None,
        'created_at': s.created_at.isoformat(),
        'updated_at': s.updated_at.isoformat(),
    }
    if with_config:
        data['configurazione'] = s.configurazione
    return data


def _parse_json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}'), None
    except (ValueError, UnicodeDecodeError) as e:
        return None, HttpResponseBadRequest(f'JSON non valido: {e}')


@_staff_required
@require_http_methods(['GET', 'POST'])
def sets_list(request):
    """GET: elenco set (senza configurazione, per il dropdown).
    POST: crea nuovo set. Body: {nome, descrizione?, configurazione}."""
    if request.method == 'GET':
        qs = SetCartellini.objects.all().select_related('creato_da')
        return JsonResponse({'sets': [_serialize(s) for s in qs]})

    payload, err = _parse_json(request)
    if err:
        return err
    nome = (payload.get('nome') or '').strip()
    config = payload.get('configurazione')
    if not nome:
        return HttpResponseBadRequest('Campo "nome" obbligatorio.')
    if not isinstance(config, dict):
        return HttpResponseBadRequest('Campo "configurazione" deve essere un oggetto JSON.')
    s = SetCartellini.objects.create(
        nome=nome[:120],
        descrizione=(payload.get('descrizione') or '')[:300],
        configurazione=config,
        creato_da=request.user,
    )
    return JsonResponse(_serialize(s, with_config=True), status=201)


@_staff_required
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def sets_detail(request, pk: int):
    """GET: dettaglio (con configurazione).
    PATCH: aggiorna. Body: {nome?, descrizione?, configurazione?}.
    DELETE: elimina."""
    s = get_object_or_404(SetCartellini, pk=pk)

    if request.method == 'GET':
        return JsonResponse(_serialize(s, with_config=True))

    if request.method == 'DELETE':
        s.delete()
        return JsonResponse({'ok': True})

    # PATCH
    payload, err = _parse_json(request)
    if err:
        return err
    if 'nome' in payload:
        nome = (payload.get('nome') or '').strip()
        if not nome:
            return HttpResponseBadRequest('Il nome non puo essere vuoto.')
        s.nome = nome[:120]
    if 'descrizione' in payload:
        s.descrizione = (payload.get('descrizione') or '')[:300]
    if 'configurazione' in payload:
        config = payload.get('configurazione')
        if not isinstance(config, dict):
            return HttpResponseBadRequest('Campo "configurazione" deve essere un oggetto JSON.')
        s.configurazione = config
    s.save()
    return JsonResponse(_serialize(s, with_config=True))


@_staff_required
@require_http_methods(['POST'])
def sets_duplica(request, pk: int):
    """POST: duplica un set. Crea una copia con nome 'Copia di <nome>'."""
    src = get_object_or_404(SetCartellini, pk=pk)
    copy = SetCartellini.objects.create(
        nome=f'Copia di {src.nome}'[:120],
        descrizione=src.descrizione,
        configurazione=src.configurazione,
        creato_da=request.user,
    )
    return JsonResponse(_serialize(copy, with_config=True), status=201)
