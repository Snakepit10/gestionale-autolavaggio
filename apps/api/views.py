"""View REST e webhook per messaggi WhatsApp.

Tutto qui dentro tocca i modelli `apps.messaggi.{Conversazione,Messaggio}
WhatsApp` e usa le utility di `apps.clients.whatsapp` per invii API Meta.

Layout:
- `whatsapp_webhook`: l'endpoint che Meta chiama. GET verifica setup,
  POST riceve messaggi e status updates. csrf_exempt + verifica
  X-Hub-Signature-256 con HMAC-SHA256(app_secret, raw_body).
- `lista_conversazioni`, `dettaglio_conversazione`, `invia_messaggio`,
  `segna_letti`, `aggancia_cliente`: API JSON per l'inbox UI.
- Helper interni: `_verify_signature`, `_handle_incoming`,
  `_handle_status`, `_match_cliente_da_telefono`.

Notify realtime: dopo ogni cambio di stato rilevante, chiamiamo
`apps.api.notify.notify_group` con group `messaggi_wa` cosi' il
frontend (vedi consumer estensione) si aggiorna senza refresh.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.clienti.models import Cliente
from apps.clients import whatsapp as wa
from apps.messaggi.models import ConversazioneWhatsApp, MessaggioWhatsApp

from .notify import notify_group

logger = logging.getLogger(__name__)

_GROUP_WA = 'messaggi_wa'


# ===========================================================================
# WEBHOOK ricezione da Meta Cloud API
# ===========================================================================

def _verify_signature(request) -> bool:
    """Verifica X-Hub-Signature-256 contro APP_SECRET.

    Meta firma il body con HMAC-SHA256(app_secret). Se l'header manca o
    non corrisponde -> richiesta sospetta, scartare.
    """
    secret = settings.META_WHATSAPP_APP_SECRET
    if not secret:
        # Setup incompleto: meglio rifiutare per sicurezza
        logger.warning('META_WHATSAPP_APP_SECRET non configurato, webhook respinto')
        return False
    sig_header = request.headers.get('X-Hub-Signature-256', '')
    if not sig_header.startswith('sha256='):
        return False
    expected = hmac.new(
        secret.encode('utf-8'), request.body, hashlib.sha256
    ).hexdigest()
    received = sig_header.split('=', 1)[1]
    return hmac.compare_digest(expected, received)


def _match_cliente_da_telefono(numero_e164: str):
    """Cerca un Cliente il cui telefono (qualunque sia il formato) normalizzi
    allo stesso numero E.164. None se non trovato.

    Iteriamo solo sui clienti con telefono non vuoto. Per gestionali con
    ~migliaia di clienti basta e avanza; se in futuro la rubrica cresce,
    convertiamo telefono in field E.164 indicizzato.
    """
    if not numero_e164:
        return None
    for c in Cliente.objects.exclude(telefono='').only('id', 'telefono'):
        normalized = wa._to_e164(c.telefono)
        if normalized == numero_e164:
            return c
    return None


def _handle_incoming(payload_msg: dict, contacts: list[dict]):
    """Processa un singolo messaggio in entrata.

    Crea/aggiorna ConversazioneWhatsApp + MessaggioWhatsApp.
    Dedup tramite wa_message_id (Meta puo' rinotificare lo stesso msg).
    Pinga il frontend via channels.
    """
    from_num = payload_msg.get('from', '')  # solo cifre, senza +
    if not from_num:
        return
    e164 = wa._to_e164(from_num)
    if not e164:
        # Numero non parsabile: scartiamo silenziosamente
        return
    wa_msg_id = payload_msg.get('id', '')

    # Conversazione (get_or_create)
    conv, created = ConversazioneWhatsApp.objects.get_or_create(
        numero_e164=e164,
        defaults={'cliente': _match_cliente_da_telefono(e164)},
    )
    # Se esisteva senza cliente e ora abbiamo match, agganciamo
    if not created and conv.cliente is None:
        match = _match_cliente_da_telefono(e164)
        if match:
            conv.cliente = match

    # Estrai corpo testo. Meta puo' mandare anche image/video/document/
    # location/audio/sticker: per Phase 1 estraiamo solo text.body, gli
    # altri tipi finiscono come placeholder.
    msg_type = payload_msg.get('type', 'text')
    if msg_type == 'text':
        corpo = payload_msg.get('text', {}).get('body', '')
    else:
        corpo = f'[{msg_type}]'  # placeholder per media

    # Timestamp Meta in Unix epoch (string)
    ts_raw = payload_msg.get('timestamp', '')
    try:
        ts = timezone.make_aware(datetime.fromtimestamp(int(ts_raw)))
    except (TypeError, ValueError):
        ts = timezone.now()

    # Dedup: se questo wa_message_id e' gia' nella conv, skip
    if wa_msg_id and MessaggioWhatsApp.objects.filter(
        conversazione=conv, wa_message_id=wa_msg_id
    ).exists():
        return

    MessaggioWhatsApp.objects.create(
        conversazione=conv,
        direzione='in',
        corpo=corpo,
        wa_message_id=wa_msg_id,
        stato='received',
        timestamp_meta=ts,
    )
    conv.ultimo_incoming_il = timezone.now()
    conv.non_letti = (conv.non_letti or 0) + 1
    conv.save(update_fields=['ultimo_incoming_il', 'non_letti', 'cliente',
                             'ultimo_messaggio_il'])

    notify_group(_GROUP_WA, {
        'type': 'nuovo_messaggio_wa',
        'conv_id': conv.pk,
        'numero_e164': e164,
        'preview': corpo[:80],
        'timestamp': timezone.now().isoformat(),
    })


def _handle_status(status_obj: dict):
    """Aggiorna stato di un messaggio outgoing in base ai delivery report."""
    wa_id = status_obj.get('id', '')
    new_stato = status_obj.get('status', '')  # sent/delivered/read/failed
    if not wa_id or not new_stato:
        return
    if new_stato not in {'sent', 'delivered', 'read', 'failed'}:
        return
    try:
        msg = MessaggioWhatsApp.objects.get(wa_message_id=wa_id)
    except MessaggioWhatsApp.DoesNotExist:
        return
    # Solo "promozioni" di stato (sent -> delivered -> read), no regressioni
    rank = {'sent': 1, 'delivered': 2, 'read': 3, 'failed': 0}
    if rank.get(new_stato, 0) >= rank.get(msg.stato, 0):
        msg.stato = new_stato
        msg.save(update_fields=['stato'])
        notify_group(_GROUP_WA, {
            'type': 'aggiorna_stato_wa',
            'conv_id': msg.conversazione_id,
            'msg_id': msg.pk,
            'stato': new_stato,
        })


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    """Endpoint Meta Cloud API webhook."""
    if request.method == 'GET':
        # Verifica setup one-time
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge', '')
        if mode == 'subscribe' and token == settings.META_WHATSAPP_VERIFY_TOKEN \
                and settings.META_WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain')
        logger.warning('WhatsApp webhook verify FAILED mode=%s token_match=%s',
                       mode, token == settings.META_WHATSAPP_VERIFY_TOKEN)
        return HttpResponseForbidden('verify failed')

    # POST: messaggio/status update
    if not _verify_signature(request):
        return HttpResponseForbidden('signature invalid')

    try:
        body = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning('WhatsApp webhook body non JSON: %s', e)
        return HttpResponse('ok', status=200)  # 200 per non far retryare Meta

    try:
        for entry in body.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                contacts = value.get('contacts', [])
                # Messaggi in entrata
                for msg in value.get('messages', []):
                    _handle_incoming(msg, contacts)
                # Status update messaggi outgoing
                for status_obj in value.get('statuses', []):
                    _handle_status(status_obj)
    except Exception as e:
        # Non vogliamo che Meta retry-i in loop: log e 200
        logger.error('WhatsApp webhook errore processing: %s', e, exc_info=True)

    return HttpResponse('ok', status=200)


# ===========================================================================
# REST per la inbox /messaggi/
# ===========================================================================

def _is_staff(user):
    return user.is_authenticated and user.is_staff


def _serialize_conv(c: ConversazioneWhatsApp, ultimo: MessaggioWhatsApp | None = None) -> dict:
    nome = (c.cliente.nome if c.cliente and c.cliente.nome
            else (c.cliente.ragione_sociale if c.cliente and c.cliente.ragione_sociale
                  else 'Numero sconosciuto'))
    return {
        'id': c.pk,
        'numero_e164': c.numero_e164,
        'cliente_id': c.cliente_id,
        'cliente_nome': nome,
        'non_letti': c.non_letti,
        'ultimo_messaggio_il': c.ultimo_messaggio_il.isoformat() if c.ultimo_messaggio_il else None,
        'finestra_24h_aperta': c.finestra_24h_aperta(),
        'preview': (ultimo.corpo[:80] if ultimo else ''),
        'preview_direzione': (ultimo.direzione if ultimo else ''),
    }


def _serialize_msg(m: MessaggioWhatsApp) -> dict:
    return {
        'id': m.pk,
        'direzione': m.direzione,
        'corpo': m.corpo,
        'stato': m.stato,
        'creato_il': m.creato_il.isoformat(),
        'operatore': m.operatore.username if m.operatore else None,
    }


@login_required
@require_http_methods(['GET'])
def lista_conversazioni(request):
    if not _is_staff(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    convs = ConversazioneWhatsApp.objects.select_related('cliente').all()[:200]
    # Per ogni conv, ultimo msg per preview (1 query per conv -> tollerabile per N<=200)
    out = []
    for c in convs:
        ultimo = c.messaggi.order_by('-creato_il').first()
        out.append(_serialize_conv(c, ultimo))
    return JsonResponse({'conversazioni': out})


@login_required
@require_http_methods(['GET'])
def dettaglio_conversazione(request, pk):
    if not _is_staff(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    conv = get_object_or_404(
        ConversazioneWhatsApp.objects.select_related('cliente'), pk=pk
    )
    # Ultimi 100 messaggi in ordine cronologico
    msgs = list(conv.messaggi.order_by('-creato_il')[:100])
    msgs.reverse()
    return JsonResponse({
        'conversazione': _serialize_conv(conv, msgs[-1] if msgs else None),
        'messaggi': [_serialize_msg(m) for m in msgs],
    })


@login_required
@require_http_methods(['POST'])
def invia_messaggio(request, pk):
    if not _is_staff(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    conv = get_object_or_404(ConversazioneWhatsApp, pk=pk)
    if not conv.finestra_24h_aperta():
        return JsonResponse({
            'error': 'finestra_24h_scaduta',
            'message': 'Finestra di 24h dall\'ultimo messaggio del cliente '
                       'scaduta. Per riavviare la conversazione invia un '
                       'template approvato.',
        }, status=400)
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'json invalido'}, status=400)
    testo = (body.get('testo') or '').strip()
    if not testo:
        return JsonResponse({'error': 'testo vuoto'}, status=400)

    ok, wa_id_or_err = wa._send_text_blocking(conv.numero_e164, testo)
    if not ok:
        return JsonResponse({'error': wa_id_or_err}, status=502)

    m = MessaggioWhatsApp.objects.create(
        conversazione=conv,
        direzione='out',
        corpo=testo,
        wa_message_id=wa_id_or_err,
        stato='sent',
        operatore=request.user,
    )
    # ultimo_messaggio_il aggiornato da auto_now sulla conversazione al save()
    conv.save(update_fields=['ultimo_messaggio_il'])

    notify_group(_GROUP_WA, {
        'type': 'nuovo_messaggio_wa',
        'conv_id': conv.pk,
        'numero_e164': conv.numero_e164,
        'preview': testo[:80],
        'direzione': 'out',
        'timestamp': m.creato_il.isoformat(),
    })

    return JsonResponse({'ok': True, 'messaggio': _serialize_msg(m)})


@login_required
@require_http_methods(['POST'])
def segna_letti(request, pk):
    if not _is_staff(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    conv = get_object_or_404(ConversazioneWhatsApp, pk=pk)
    if conv.non_letti:
        conv.non_letti = 0
        conv.save(update_fields=['non_letti'])
        notify_group(_GROUP_WA, {
            'type': 'segna_letti_wa',
            'conv_id': conv.pk,
        })
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def aggancia_cliente(request, pk):
    """Associa la conversazione a un cliente esistente.

    Body JSON: {cliente_id}. Usato quando l'operatore identifica
    manualmente chi e' il numero sconosciuto.
    """
    if not _is_staff(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'json invalido'}, status=400)
    cliente_id = body.get('cliente_id')
    conv = get_object_or_404(ConversazioneWhatsApp, pk=pk)
    if cliente_id:
        try:
            conv.cliente = Cliente.objects.get(pk=cliente_id)
        except Cliente.DoesNotExist:
            return JsonResponse({'error': 'cliente non trovato'}, status=404)
    else:
        conv.cliente = None
    conv.save(update_fields=['cliente'])
    return JsonResponse({'ok': True, 'cliente_nome': (conv.cliente.nome if conv.cliente else None)})
