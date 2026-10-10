"""Pagina Configurazione > Permessi: chi vede quali sezioni del gestionale."""
import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import PermessoGruppo, PermessoUtente
from .sezioni import FISSE, GRUPPI, NOMI, SEZIONI, ha_accesso, sezioni_permesse


def _solo_permessi(view):
    def wrapper(request, *args, **kwargs):
        if not ha_accesso(request.user, 'permessi'):
            return JsonResponse({'success': False, 'error': 'Non hai accesso ai Permessi'}, status=403)
        return view(request, *args, **kwargs)
    return login_required(wrapper)


def _json(request):
    try:
        return json.loads(request.body or '{}')
    except ValueError:
        return None


def _utenti_gestionale():
    return (User.objects.filter(Q(is_staff=True) | Q(groups__isnull=False) | Q(permessi_sezione__isnull=False))
            .filter(is_active=True).distinct().prefetch_related('groups').order_by('username'))


@_solo_permessi
def matrice(request):
    gruppi = [Group.objects.get_or_create(name=nome)[0] for nome in GRUPPI]
    spuntate = set(PermessoGruppo.objects.values_list('sezione', 'gruppo_id'))
    righe = []
    for chiave, nome, descrizione, _ in SEZIONI:
        fissa = FISSE.get(chiave)
        righe.append({
            'chiave': chiave, 'nome': nome, 'descrizione': descrizione, 'fissa': bool(fissa),
            'celle': [{'gruppo': g, 'attivo': (g.name in fissa) if fissa else (chiave, g.pk) in spuntate}
                      for g in gruppi],
        })
    utenti = list(_utenti_gestionale())
    for u in utenti:
        u.sezioni_effettive = [NOMI[c] for c, _, _, _ in SEZIONI if c in sezioni_permesse(u)]
        u.nomi_gruppi = [g.name for g in u.groups.all()]
    return render(request, 'auth_system/permessi.html', {
        'gruppi': gruppi, 'righe': righe, 'utenti': utenti,
        'sezioni_modificabili': [(c, n) for c, n, _, _ in SEZIONI if c not in FISSE],
        'eccezioni': PermessoUtente.objects.select_related('user').order_by('user__username', 'sezione'),
    })


@_solo_permessi
def api_gruppo(request):
    """POST {sezione, gruppo_id, attivo}: spunta o toglie la sezione al gruppo."""
    dati = _json(request) if request.method == 'POST' else None
    if not dati or dati.get('sezione') not in NOMI or dati.get('sezione') in FISSE:
        return JsonResponse({'success': False, 'error': 'Sezione non valida'}, status=400)
    gruppo = get_object_or_404(Group, pk=dati.get('gruppo_id'), name__in=GRUPPI)
    if dati.get('attivo'):
        PermessoGruppo.objects.get_or_create(sezione=dati['sezione'], gruppo=gruppo)
    else:
        PermessoGruppo.objects.filter(sezione=dati['sezione'], gruppo=gruppo).delete()
    return JsonResponse({'success': True})


@_solo_permessi
def api_eccezione(request):
    """POST {user_id, sezione, consenti}: crea o aggiorna l'eccezione."""
    dati = _json(request) if request.method == 'POST' else None
    if not dati or dati.get('sezione') not in NOMI or dati.get('sezione') in FISSE:
        return JsonResponse({'success': False, 'error': 'Sezione non valida'}, status=400)
    utente = get_object_or_404(User, pk=dati.get('user_id'))
    PermessoUtente.objects.update_or_create(
        sezione=dati['sezione'], user=utente, defaults={'consenti': bool(dati.get('consenti'))})
    return JsonResponse({'success': True})


@_solo_permessi
def api_eccezione_elimina(request, pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo non consentito'}, status=405)
    PermessoUtente.objects.filter(pk=pk).delete()
    return JsonResponse({'success': True})
