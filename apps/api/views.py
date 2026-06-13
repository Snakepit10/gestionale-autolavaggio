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
import threading
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import (HttpResponse, JsonResponse,
                         HttpResponseForbidden, StreamingHttpResponse)
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

    # Estrai testo o media. Meta manda solo media_id per audio/foto/video/
    # document: il file binario lo scarichiamo on-demand via media_proxy
    # quando l'operatore preme play/apri nella inbox.
    msg_type_raw = payload_msg.get('type', 'text')
    # Meta usa 'audio' anche per le voce; trattiamo uguale.
    if msg_type_raw == 'voice':
        msg_type_raw = 'audio'
    media_id = ''
    media_mime = ''
    if msg_type_raw == 'text':
        corpo = payload_msg.get('text', {}).get('body', '')
        media_type = 'text'
    elif msg_type_raw in ('audio', 'image', 'video', 'document', 'sticker'):
        media_obj = payload_msg.get(msg_type_raw, {}) or {}
        media_id = media_obj.get('id', '')
        media_mime = media_obj.get('mime_type', '')
        caption = media_obj.get('caption', '') or ''
        label = {
            'audio': 'Audio', 'image': 'Foto', 'video': 'Video',
            'document': 'Documento', 'sticker': 'Sticker',
        }[msg_type_raw]
        corpo = f'[{label}]' + (f' {caption}' if caption else '')
        media_type = msg_type_raw
    elif msg_type_raw == 'location':
        loc = payload_msg.get('location', {}) or {}
        corpo = f"[Posizione] {loc.get('latitude','?')},{loc.get('longitude','?')}"
        media_type = 'location'
    elif msg_type_raw == 'button':
        # Quick Reply Button cliccato su un template Meta (formato legacy)
        # Meta manda: { type: 'button', button: { text: 'Va bene', payload: '...' } }
        btn = payload_msg.get('button', {}) or {}
        corpo = btn.get('text', '') or btn.get('payload', '') or '[button]'
        media_type = 'text'
    elif msg_type_raw == 'interactive':
        # Risposta a interactive message (button reply o list reply)
        # Meta manda: { type: 'interactive', interactive: { type: 'button_reply',
        # button_reply: { id, title } } }
        inter = payload_msg.get('interactive', {}) or {}
        inter_type = inter.get('type', '')
        if inter_type == 'button_reply':
            corpo = (inter.get('button_reply', {}) or {}).get('title', '') or '[button]'
        elif inter_type == 'list_reply':
            corpo = (inter.get('list_reply', {}) or {}).get('title', '') or '[list]'
        else:
            corpo = f'[{inter_type or "interactive"}]'
        media_type = 'text'
    else:
        # Tipi sconosciuti: salva placeholder
        corpo = f'[{msg_type_raw}]'
        media_type = 'text'

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
        media_type=media_type,
        media_id=media_id,
        media_mime=media_mime,
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

    # Log diagnostico per debug Quick Reply: raw payload type + corpo finale
    logger.info(
        'WA incoming: type=%s -> corpo=%r media_type=%s conv=%d cliente=%s',
        msg_type_raw, corpo, media_type, conv.pk,
        conv.cliente_id if conv.cliente_id else 'NESSUNO',
    )

    # Auto-handler Quick Reply: se il messaggio e' la risposta a un
    # template prenotazione_proposta_orario, conferma o annulla
    # automaticamente la prenotazione del cliente. Lanciato in thread
    # daemon per non bloccare la risposta al webhook (Meta retrya
    # se non rispondiamo entro pochi secondi).
    # Strip + check case-insensitive per essere robusti contro spazi
    # extra o variazioni che Meta a volte introduce.
    corpo_norm = (corpo or '').strip()
    if media_type == 'text' and corpo_norm in _QUICK_REPLY_AZIONI:
        logger.info('Quick reply detected: %r -> spawn handler conv=%d',
                   corpo_norm, conv.pk)
        threading.Thread(
            target=_handle_quick_reply,
            args=(conv.pk, corpo_norm),
            daemon=True,
        ).start()


# Quick Reply buttons del template prenotazione_proposta_orario.
# Quando il cliente clicca uno di questi, scattano azioni automatiche
# sulla prenotazione in_attesa piu' recente del cliente.
# Nomi esatti come approvati su Meta WhatsApp Manager.
_QUICK_REPLY_AZIONI = {'Confermo', 'No, non riesco'}


def _handle_quick_reply(conv_pk: int, testo: str):
    """Esegue auto-azione sulla prenotazione in_attesa del cliente in
    base al Quick Reply Button cliccato.

    Eseguito in daemon thread separato:
    - 'Confermo' -> stato='confermata', notifica conferma
    - 'No, non riesco' -> stato='annullata', manda elenco slot
      alternativi via testo libero (finestra 24h aperta -> niente
      template necessario)
    """
    logger.info('handle_quick_reply START conv=%s testo=%r', conv_pk, testo)
    try:
        from apps.prenotazioni.models import Prenotazione
        from apps.clients.notifications import notifica_prenotazione_confermata
        from apps.clients import whatsapp as wa_module

        conv = ConversazioneWhatsApp.objects.select_related('cliente').filter(pk=conv_pk).first()
        if not conv:
            logger.warning('Quick reply: conv=%s non trovata', conv_pk)
            return

        # Cerca prenotazione in_attesa: prima per cliente agganciato, poi
        # come fallback per telefono_contatto matchato col numero E.164
        # della conversazione (cliente potrebbe non essere agganciato).
        p = None
        if conv.cliente:
            p = (
                Prenotazione.objects
                .filter(cliente=conv.cliente, stato='in_attesa', ordine__isnull=True)
                .select_related('slot')
                .order_by('-creata_il')
                .first()
            )
            if p:
                logger.info('Quick reply: trovata pren=%s via cliente=%s',
                           p.codice_prenotazione, conv.cliente_id)
        if not p:
            # Fallback: telefono_contatto matchato. Iteriamo i candidati
            # e normalizziamo in E.164.
            for cand in (Prenotazione.objects
                         .filter(stato='in_attesa', ordine__isnull=True)
                         .exclude(telefono_contatto='')
                         .select_related('slot')
                         .order_by('-creata_il')[:20]):
                if wa._to_e164(cand.telefono_contatto) == conv.numero_e164:
                    p = cand
                    logger.info('Quick reply: trovata pren=%s via telefono_contatto',
                               p.codice_prenotazione)
                    break

        if not p:
            logger.warning(
                'Quick reply: NESSUNA prenotazione in_attesa per conv=%s '
                'cliente=%s numero=%s',
                conv_pk, conv.cliente_id, conv.numero_e164,
            )
            return

        if testo == 'Confermo':
            p.stato = 'confermata'
            p.proposta_inviata_il = None
            p.save(update_fields=['stato', 'proposta_inviata_il'])
            if hasattr(p.slot, 'aggiorna_contatori'):
                p.slot.aggiorna_contatori()
            notifica_prenotazione_confermata(p)
            logger.info('Auto-confermata prenotazione %s via Quick Reply "Confermo"',
                       p.codice_prenotazione)

        elif testo == 'No, non riesco':
            # Annulla e libera lo slot
            vecchio_slot = p.slot
            p.stato = 'annullata'
            p.proposta_inviata_il = None
            nota = (p.nota_interna or '').strip()
            p.nota_interna = (nota + '\n' if nota else '') + 'Rifiutata dal cliente via WhatsApp (Quick Reply "No, non riesco").'
            p.save(update_fields=['stato', 'nota_interna', 'proposta_inviata_il'])
            if hasattr(vecchio_slot, 'aggiorna_contatori'):
                vecchio_slot.aggiorna_contatori()
            logger.info('Auto-annullata prenotazione %s via Quick Reply "No, non riesco"',
                       p.codice_prenotazione)
            # Proponi al cliente slot alternativi via testo libero
            _proponi_slot_alternativi_text(conv.numero_e164)
    except Exception as e:
        logger.warning('handle_quick_reply error conv=%s testo=%s: %s',
                      conv_pk, testo, e, exc_info=True)


def _proponi_slot_alternativi_text(to_e164: str) -> None:
    """Manda al cliente un testo libero con i prossimi slot disponibili.

    Funziona solo entro 24h dall'ultimo messaggio del cliente -- e' il
    caso giusto dopo un Quick Reply (il messaggio "No, non riesco" e'
    appena entrato, finestra 24h fresca).

    Cerca slot liberi nei prossimi 4 giorni (oggi incluso), fino a 8
    slot totali, e li formatta in modo leggibile su WhatsApp.
    """
    try:
        from apps.prenotazioni.models import SlotPrenotazione
        from apps.clients import whatsapp as wa_module

        oggi = timezone.localtime(timezone.now()).date()
        giorni_con_slot = []
        totale_slot = 0
        for delta in range(0, 4):
            data = oggi + timedelta(days=delta)
            qs = (
                SlotPrenotazione.objects
                .filter(data=data, disponibile=True)
                .annotate(liberi=F('max_prenotazioni') - F('prenotazioni_attuali'))
                .filter(liberi__gt=0)
                .order_by('ora_inizio')[:6]
            )
            qs_list = list(qs)
            if qs_list:
                giorni_con_slot.append((data, qs_list))
                totale_slot += len(qs_list)
            if totale_slot >= 8:
                break

        if not giorni_con_slot:
            text = (
                "Capito! Purtroppo per i prossimi giorni siamo al completo.\n\n"
                "Chiamaci al 379 233 7051 e troveremo insieme un orario per te."
            )
        else:
            giorni_it = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
            lines = ["Capito! Ecco i prossimi slot disponibili:\n"]
            for data, ss in giorni_con_slot:
                gn = giorni_it[data.weekday()]
                ore = ', '.join(s.ora_inizio.strftime('%H:%M') for s in ss)
                lines.append(f"📅 {gn} {data.strftime('%d/%m')}: {ore}")
            lines.append(
                "\nScrivici qui l'orario che preferisci, oppure chiamaci "
                "al 379 233 7051."
            )
            text = '\n'.join(lines)

        wa_module._send_text_blocking(to_e164, text)
    except Exception as e:
        logger.warning('proponi_slot_alternativi error to=%s: %s', to_e164, e)


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
    # Nome+cognome (o ragione sociale) usando la property `nome_completo`
    # gia' esistente sul modello Cliente. Per numeri sconosciuti -> default.
    if c.cliente:
        nome = c.cliente.nome_completo or 'Cliente senza nome'
    else:
        nome = 'Numero sconosciuto'
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
        'media_type': m.media_type or 'text',
        'media_mime': m.media_mime or '',
        'has_media': bool(m.media_id),
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
    return JsonResponse({'ok': True, 'cliente_nome': (conv.cliente.nome_completo if conv.cliente else None)})


# ===========================================================================
# MEDIA PROXY: scarica audio/foto/video da Meta on-demand
# ===========================================================================
# Meta non manda il binario nel webhook ma solo un media_id. Per ottenerlo
# servono 2 step autenticati col bearer token:
#   1. GET /<media_id>           -> ritorna JSON con un URL temporaneo (~5min)
#   2. GET <quel URL>             -> ritorna il binario
# Il browser dell'operatore non puo' fare il passo 1 da solo (richiede il
# token segreto del System User), quindi facciamo proxy noi: il browser
# punta a /api/whatsapp/media/<msg_id>/ e noi rispondiamo col binario.

@login_required
@require_http_methods(['GET'])
def media_proxy(request, msg_id):
    if not _is_staff(request.user):
        return HttpResponse(status=403)
    msg = get_object_or_404(MessaggioWhatsApp, pk=msg_id)
    if not msg.media_id:
        return HttpResponse(status=404)
    if not settings.WHATSAPP_ENABLED:
        return HttpResponse(status=503)

    headers = {'Authorization': f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}'}
    info_url = (
        f'https://graph.facebook.com/{settings.META_WHATSAPP_API_VERSION}'
        f'/{msg.media_id}'
    )
    try:
        r1 = requests.get(info_url, headers=headers, timeout=10)
        if r1.status_code >= 400:
            logger.warning('media_proxy info fallito (%s) media_id=%s: %s',
                          r1.status_code, msg.media_id, r1.text[:200])
            return HttpResponse(status=502)
        data = r1.json()
        media_url = data.get('url', '')
    except (requests.RequestException, ValueError) as e:
        logger.warning('media_proxy info error media_id=%s: %s', msg.media_id, e)
        return HttpResponse(status=502)

    if not media_url:
        return HttpResponse(status=404)

    try:
        # stream=True: scarichiamo a chunks per non caricare in RAM file grandi
        r2 = requests.get(media_url, headers=headers, timeout=30, stream=True)
        if r2.status_code >= 400:
            return HttpResponse(status=502)
    except requests.RequestException as e:
        logger.warning('media_proxy fetch error: %s', e)
        return HttpResponse(status=502)

    content_type = msg.media_mime or r2.headers.get('Content-Type', 'application/octet-stream')

    def stream():
        for chunk in r2.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    resp = StreamingHttpResponse(stream(), content_type=content_type)
    # Cache lato browser: stessa risposta media valida ~1h
    resp['Cache-Control'] = 'private, max-age=3600'
    return resp
