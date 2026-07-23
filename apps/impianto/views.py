"""Endpoint di collaudo del modulo impianto."""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .mqtt import moneta_virtuale


@csrf_exempt
def test_moneta(request):
    """POST /api/test/moneta/ - collaudo manuale della moneta virtuale.

    Riservato allo staff loggato. Body JSON:
        {"nodo": "pista2", "impulsi": 1}
    `impulsi` opzionale (default 1, max 10 per sicurezza in collaudo).
    """
    if not (request.user.is_authenticated and
            (request.user.is_staff or request.user.groups.exists())):
        return JsonResponse({'error': 'Riservato agli operatori.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'Usa POST.'}, status=405)

    try:
        dati = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'Body JSON non valido.'}, status=400)

    nodo = (dati.get('nodo') or '').strip()
    if not nodo or '/' in nodo:
        return JsonResponse({'error': "Campo 'nodo' mancante o non valido "
                                      "(es. 'pista2')."}, status=400)
    try:
        impulsi = int(dati.get('impulsi', 1))
    except (TypeError, ValueError):
        return JsonResponse({'error': "'impulsi' deve essere un intero."}, status=400)
    if not 1 <= impulsi <= 10:
        return JsonResponse({'error': "'impulsi' deve essere tra 1 e 10."}, status=400)

    ok, messaggio = moneta_virtuale(nodo, impulsi)
    return JsonResponse({'ok': ok, 'messaggio': messaggio},
                        status=200 if ok else 502)
