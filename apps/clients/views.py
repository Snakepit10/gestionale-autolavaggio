"""Lato cliente / frontend pubblico.

- Landing pubblica
- Registrazione
- Dashboard cliente
- Flusso prenotazione (catalogo + slot picker + conferma)
"""
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from apps.clienti.models import Cliente
from apps.core.models import ServizioProdotto, Categoria
from apps.prenotazioni.models import (
    Prenotazione, SlotPrenotazione, ConfigurazioneSlot,
)

from .forms import RegistrazioneClienteForm


def _is_cliente(user):
    return user.is_authenticated and hasattr(user, 'cliente')


def _is_staff_app(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def root_redirect(request):
    """Smart routing su /:
    - Staff/admin -> dashboard staff (core:home)
    - Cliente loggato -> dashboard cliente
    - Anonimo -> landing pubblica
    """
    if _is_staff_app(request.user):
        return redirect('core:home')
    if _is_cliente(request.user):
        return redirect('clients:dashboard')
    return redirect('clients:landing')


def landing(request):
    """Landing page pubblica con CTA login/registrazione/prenotazione.

    Non espone item ne' prezzi: presenta i servizi come testo discorsivo
    discreto. Per il catalogo dettagliato l'utente deve essere loggato.
    """
    return render(request, 'clients/landing.html', {})


def register(request):
    """Registrazione cliente privato."""
    if request.user.is_authenticated:
        return redirect('clients:dashboard')

    if request.method == 'POST':
        form = RegistrazioneClienteForm(request.POST)
        if form.is_valid():
            user, cliente = form.save()
            login(request, user)
            messages.success(request, f"Benvenuto {cliente.nome}! Account creato.")
            return redirect('clients:dashboard')
    else:
        form = RegistrazioneClienteForm()

    return render(request, 'clients/register.html', {'form': form})


@login_required
def dashboard(request):
    """Area cliente: prossime prenotazioni, ultimi ordini, punti fedelta."""
    if _is_staff_app(request.user) and not _is_cliente(request.user):
        return redirect('core:home')

    cliente = getattr(request.user, 'cliente', None)
    if not cliente:
        messages.warning(request, "Account senza profilo cliente. Contatta l'autolavaggio.")
        return redirect('clients:landing')

    oggi = timezone.now().date()
    prenotazioni_prossime = (
        Prenotazione.objects.filter(
            cliente=cliente,
            slot__data__gte=oggi,
            stato__in=['confermata', 'in_attesa'],
        )
        .select_related('slot')
        .prefetch_related('servizi')
        .order_by('slot__data', 'slot__ora_inizio')[:5]
    )
    prenotazioni_passate = (
        Prenotazione.objects.filter(cliente=cliente, slot__data__lt=oggi)
        .select_related('slot')
        .prefetch_related('servizi')
        .order_by('-slot__data')[:5]
    )

    # Punti fedelta (se esiste il modello)
    punti_totali = 0
    try:
        from apps.clienti.models import PuntiFedelta
        pf = PuntiFedelta.objects.filter(cliente=cliente).first()
        if pf:
            punti_totali = pf.punti_disponibili if hasattr(pf, 'punti_disponibili') else 0
    except Exception:
        pass

    return render(request, 'clients/dashboard.html', {
        'cliente': cliente,
        'prenotazioni_prossime': prenotazioni_prossime,
        'prenotazioni_passate': prenotazioni_passate,
        'punti_totali': punti_totali,
    })


@ensure_csrf_cookie
def booking(request):
    """Catalogo + wizard prenotazione cliente (lazy-register).

    Sia anonimi che loggati vedono lo stesso wizard. Anonimi devono
    inserire i dati personali nello step 3 e possono opzionalmente
    creare un account con password nello step 4. Loggati saltano
    direttamente alla conferma.
    """
    # Servizi base raggruppati per categoria (Esterno, Interno, Sottoscocca,
    # ecc.): un wizard step per ogni categoria che ha almeno un servizio
    # pubblico. L'ordine degli step segue Categoria.ordine_visualizzazione.
    # prefetch_related('categorie_aggiuntive') per supportare gli item
    # multicategoria che compaiono in piu' step.
    servizi_qs = list(
        ServizioProdotto.objects
        .filter(attivo=True, tipo='servizio', is_supplemento=False, mostra_pubblico=True)
        .select_related('categoria')
        .prefetch_related('categorie_aggiuntive')
        .order_by('categoria__ordine_visualizzazione', 'ordine_visualizzazione', 'titolo')
    )

    # Mappa "categoria -> [servizi]": un servizio appare in ogni categoria
    # a cui appartiene (primaria + aggiuntive). Ordine: ordine_visualizzazione.
    cat_ids_in_uso = []
    per_cat = {}
    for s in servizi_qs:
        cat_ids = [s.categoria_id] + [c.id for c in s.categorie_aggiuntive.all()]
        for cid in cat_ids:
            if cid not in per_cat:
                per_cat[cid] = []
                cat_ids_in_uso.append(cid)
            # Evita duplicati se primary == aggiuntiva (defensive)
            if s not in per_cat[cid]:
                per_cat[cid].append(s)
    cat_map = {
        c.id: c for c in Categoria.objects.filter(id__in=cat_ids_in_uso)
        .order_by('ordine_visualizzazione', 'nome')
    }
    # Ordina cat_ids_in_uso per ordine_visualizzazione della categoria
    categorie_step = [cat_map[i] for i in sorted(cat_ids_in_uso, key=lambda i: (cat_map[i].ordine_visualizzazione if i in cat_map else 999, cat_map[i].nome if i in cat_map else '')) if i in cat_map]
    # Lista (categoria, [servizi]) pre-renderizzata per il template
    servizi_per_categoria = [(c, per_cat[c.id]) for c in categorie_step]
    # Manteniamo anche `servizi` flat per compat con codice JS esistente
    servizi = servizi_qs
    # Gli step Extra/Profumazione sono stati rimossi dal wizard:
    # i relativi item (servizi extra, prodotti scaffale) vengono ora
    # gestiti come categorie normali con mostra_pubblico=True. Il flag
    # ServizioProdotto.proponi_in_upsell resta in modello per
    # compatibilita' ma non e' piu' usato qui.
    return render(request, 'clients/booking.html', {
        'categorie_step': categorie_step,
        'servizi': servizi,
        'servizi_per_categoria': servizi_per_categoria,
    })


def slot_disponibili_pub(request):
    """API JSON: slot disponibili per data (riusa logica esistente)."""
    data_str = request.GET.get('data')
    if not data_str:
        return JsonResponse({'error': 'Parametro data mancante'}, status=400)
    try:
        data_richiesta = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data non valida'}, status=400)

    # Genera slot da configurazione se non esistono
    giorno_settimana = data_richiesta.weekday()
    for config in ConfigurazioneSlot.objects.filter(
        giorno_settimana=giorno_settimana, attivo=True
    ):
        config.genera_slot_per_data(data_richiesta)

    now = timezone.localtime(timezone.now())
    is_oggi = data_richiesta == now.date()

    slot_qs = SlotPrenotazione.objects.filter(
        data=data_richiesta, disponibile=True
    ).order_by('ora_inizio')
    out = []
    for s in slot_qs:
        # Lato cliente: ogni slot e' esclusivo. Se gia esiste UNA
        # prenotazione attiva per quella fascia oraria, non e' piu
        # selezionabile (indipendentemente da max_prenotazioni interno).
        if s.prenotazioni_attuali > 0:
            continue
        is_past = is_oggi and s.ora_inizio < now.time()
        if is_past:
            continue
        out.append({
            'id': s.id,
            'ora_inizio': s.ora_inizio.strftime('%H:%M'),
            'ora_fine': s.ora_fine.strftime('%H:%M'),
        })
    return JsonResponse({'slot': out})


def catalogo_upsell(request):
    """API JSON: catalogo upsell per la sezione "Aggiungi extra" del
    riepilogo prenotazione.

    GET /app/api/upsell/?servizi_scelti=1,4
    Querystring:
      - servizi_scelti: csv di id servizi gia' selezionati nello step 1.
        Usato sia per escluderli dal catalogo (sono gia' nel carrello),
        sia per filtrare gli upsell legati a servizi base specifici via
        ServizioProdotto.upsell_per.

    Risposta:
      {
        "servizi_extra": [{id, titolo, prezzo, durata_minuti, descrizione, immagine}, ...],
        "prodotti":      [{id, titolo, prezzo, descrizione, immagine}, ...]
      }

    Logica filtro:
      proponi_in_upsell=True AND attivo=True
      AND NOT IN (servizi_scelti)
      AND (upsell_per vuoto                  -- universale
           OR upsell_per intersect servizi_scelti)  -- mirato
    """
    from django.db.models import Q

    raw = (request.GET.get('servizi_scelti') or '').strip()
    if raw:
        try:
            servizi_scelti_ids = [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            return JsonResponse({'error': 'servizi_scelti non valido'}, status=400)
    else:
        servizi_scelti_ids = []

    items = (
        ServizioProdotto.objects
        .filter(proponi_in_upsell=True, attivo=True)
        .exclude(id__in=servizi_scelti_ids)
        .filter(
            Q(upsell_per__isnull=True)
            | Q(upsell_per__in=servizi_scelti_ids)
        )
        .distinct()
        .order_by('ordine_upsell', 'titolo')
    )

    def _serialize(it):
        # immagine: tentiamo i nomi piu' comuni; se nessuno esiste,
        # campo vuoto. Il frontend mostra un fallback grafico.
        img = ''
        for field in ('immagine', 'foto', 'thumbnail'):
            val = getattr(it, field, None)
            if val:
                try:
                    img = val.url
                except (ValueError, AttributeError):
                    img = ''
                break
        return {
            'id': it.id,
            'titolo': it.titolo,
            'prezzo': str(it.prezzo),
            'descrizione': it.descrizione or '',
            'durata_minuti': it.durata_minuti if it.tipo == 'servizio' else None,
            'immagine': img,
        }

    return JsonResponse({
        'servizi_extra': [_serialize(i) for i in items if i.tipo == 'servizio'],
        'prodotti': [_serialize(i) for i in items if i.tipo == 'prodotto'],
    })


@require_POST
def crea_prenotazione_pub(request):
    """Crea prenotazione cliente con flusso lazy-register.

    Body JSON:
        {
          data, ora,
          servizi: [id, ...],
          tipo_auto, nota,
          # Se NON loggato:
          nome, cognome, email, telefono,
          password (opzionale: se presente crea User)
        }

    Nasce sempre con stato='in_attesa' (richiede conferma operatore).
    """
    from django.contrib.auth.models import User
    from django.contrib.auth import login as auth_login
    from django.db import transaction
    from .notifications import notifica_prenotazione_ricevuta

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON non valido'}, status=400)

    data_str = body.get('data')
    ora_str = body.get('ora')
    servizi_ids = body.get('servizi') or []
    # Upsell: servizi complementari scelti nello step di riepilogo
    # (es. "Aspirazione interni" aggiunta a un Lavaggio esterno).
    # Validati con stesso filtro dei servizi base + proponi_in_upsell.
    servizi_extra_ids = body.get('servizi_extra') or []
    # Upsell: prodotti da scaffale con quantita' (profumatore x2, ecc.).
    # Lista di {id, quantita}. Salvati in PrenotazioneProdotto con
    # snapshot del prezzo corrente.
    prodotti_raw = body.get('prodotti') or []
    tipo_auto = (body.get('tipo_auto') or '').strip()
    nota = (body.get('nota') or '').strip()

    # Dati personali (servono se non loggato)
    nome = (body.get('nome') or '').strip()
    cognome = (body.get('cognome') or '').strip()
    email = (body.get('email') or '').strip().lower()
    telefono = (body.get('telefono') or '').strip()
    password = body.get('password') or ''  # opzionale, no strip su pw

    if not data_str or not ora_str:
        return JsonResponse({'error': 'Data e ora obbligatorie'}, status=400)
    if not servizi_ids:
        return JsonResponse({'error': 'Seleziona almeno un servizio'}, status=400)

    try:
        data_p = datetime.strptime(data_str, '%Y-%m-%d').date()
        ora_inizio = datetime.strptime(ora_str, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Formato data/ora non valido'}, status=400)

    servizi = list(
        ServizioProdotto.objects.filter(
            id__in=servizi_ids, attivo=True, tipo='servizio', mostra_pubblico=True
        )
    )
    if not servizi:
        return JsonResponse({'error': 'Servizi non trovati'}, status=400)

    # Servizi extra (upsell): validati separatamente con proponi_in_upsell.
    # Vengono uniti ai servizi base in un singolo set prima del save del
    # M2M, cosi' niente duplicati anche se qualcuno appare in entrambi
    # gli array del payload.
    servizi_extra = []
    if servizi_extra_ids:
        servizi_extra = list(
            ServizioProdotto.objects.filter(
                id__in=servizi_extra_ids,
                attivo=True, tipo='servizio',
                proponi_in_upsell=True,
            )
        )

    # Prodotti upsell: validati uno per uno con quantita' >= 1.
    # Costruiamo una mappa {servizio_prodotto: quantita} per il save
    # successivo. Se quantita <= 0 si ignora silenziosamente l'item.
    prodotti_validi = []
    if prodotti_raw:
        ids_richiesti = []
        qty_richieste = {}
        for item in prodotti_raw:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get('id'))
                qty = int(item.get('quantita') or 0)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'Prodotto non valido'}, status=400)
            if qty <= 0:
                continue
            ids_richiesti.append(pid)
            qty_richieste[pid] = qty
        if ids_richiesti:
            prodotti_db = ServizioProdotto.objects.filter(
                id__in=ids_richiesti,
                attivo=True, tipo='prodotto',
                proponi_in_upsell=True,
            )
            prodotti_validi = [(p, qty_richieste[p.pk]) for p in prodotti_db]

    # Durata calcolata sui servizi (base + extra). I prodotti non
    # consumano tempo di lavorazione.
    tutti_servizi = {s.pk: s for s in servizi + servizi_extra}.values()
    durata = sum(s.durata_minuti or 30 for s in tutti_servizi)

    # ---------- Determina cliente ----------
    cliente = None
    user_creato = None
    if request.user.is_authenticated and hasattr(request.user, 'cliente'):
        cliente = request.user.cliente
    elif request.user.is_authenticated:
        # Loggato ma NON cliente (operatore/staff). Non permettere il
        # flusso guest (sostituirebbe la sessione) e segnala chiaramente.
        return JsonResponse({
            'error': (
                'Sei loggato come operatore. Esci dal tuo account per '
                'prenotare come cliente, oppure usa un altro browser/incognito.'
            ),
        }, status=403)
    else:
        # Guest: serve almeno nome+cognome+(email o telefono)
        if not nome or not cognome:
            return JsonResponse({'error': 'Nome e cognome obbligatori'}, status=400)
        if not email and not telefono:
            return JsonResponse({'error': 'Email o telefono obbligatori'}, status=400)

        # Verifica email duplicata (User o Cliente). Se utente vuole pw,
        # ma email e' gia registrata, gli dice di fare login.
        if email and password:
            if User.objects.filter(email__iexact=email).exists():
                return JsonResponse({
                    'error': 'Email gia registrata. Accedi prima di prenotare.',
                    'duplicate_email': True,
                }, status=400)

        with transaction.atomic():
            # Tenta riuso Cliente esistente per email/telefono
            existing_cliente = None
            if email:
                existing_cliente = Cliente.objects.filter(email__iexact=email).first()
            if not existing_cliente and telefono:
                existing_cliente = Cliente.objects.filter(telefono=telefono).first()

            if existing_cliente:
                cliente = existing_cliente
                # Aggiorna campi mancanti / cambiati. Mai sovrascrivere
                # email se gia presente con una differente (potrebbe
                # essere account di un altro cliente con lo stesso tel).
                changed = []
                if email and not cliente.email:
                    cliente.email = email
                    changed.append('email')
                if telefono and not cliente.telefono:
                    cliente.telefono = telefono
                    changed.append('telefono')
                if not cliente.nome and nome:
                    cliente.nome = nome
                    changed.append('nome')
                if not cliente.cognome and cognome:
                    cliente.cognome = cognome
                    changed.append('cognome')
                if changed:
                    cliente.save(update_fields=changed)
            else:
                cliente = Cliente.objects.create(
                    tipo='privato',
                    nome=nome,
                    cognome=cognome,
                    email=email or None,
                    telefono=telefono,
                )

            # Se richiesto crea User per accesso futuro
            if password and email and not cliente.user_id:
                user_creato = User.objects.create_user(
                    username=email,
                    email=email,
                    first_name=nome,
                    last_name=cognome,
                    password=password,
                )
                cliente.user = user_creato
                cliente.save(update_fields=['user'])

            # Memorizza email scelta per la prenotazione (potrebbe
            # differire da cliente.email se cliente esisteva gia con
            # email diversa). Usata per la notifica.
            email_per_notifica = email or cliente.email or ''

    # ---------- Slot ----------
    ora_fine_dt = datetime.combine(data_p, ora_inizio) + timedelta(minutes=durata)
    slot, _ = SlotPrenotazione.objects.get_or_create(
        data=data_p, ora_inizio=ora_inizio,
        defaults={
            'ora_fine': ora_fine_dt.time(),
            'max_prenotazioni': 1,
            'prenotazioni_attuali': 0,
            'disponibile': True,
        },
    )
    if slot.prenotazioni_attuali > 0:
        return JsonResponse({
            'error': 'Slot non piu disponibile, e\' stato appena prenotato. Scegli un altro orario.',
        }, status=400)

    # ---------- Crea prenotazione in attesa di conferma operatore ----------
    # email/telefono di contatto: usa quelli inseriti dal guest, oppure
    # quelli del Cliente esistente. Salvati sulla Prenotazione cosi le
    # notifiche future arrivano sempre all'indirizzo giusto.
    contact_email = email or (cliente.email or '')
    contact_telefono = telefono or (cliente.telefono or '')

    prenotazione = Prenotazione.objects.create(
        cliente=cliente,
        slot=slot,
        durata_stimata_minuti=durata,
        stato='in_attesa',
        tipo_auto=tipo_auto,
        nota_cliente=nota,
        email_contatto=contact_email,
        telefono_contatto=contact_telefono,
    )
    # M2M servizi: base + extra. set() gestisce sia INSERT che UPDATE
    # dei legami.
    prenotazione.servizi.set(list(tutti_servizi))

    # Prodotti upsell: una riga PrenotazioneProdotto per ognuno con il
    # prezzo "fotografato" al momento (protegge il cliente da cambi
    # listino tra prenotazione e check-in).
    if prodotti_validi:
        from apps.prenotazioni.models import PrenotazioneProdotto
        PrenotazioneProdotto.objects.bulk_create([
            PrenotazioneProdotto(
                prenotazione=prenotazione,
                servizio_prodotto=prodotto,
                quantita=qty,
                prezzo_unitario=prodotto.prezzo,
            )
            for prodotto, qty in prodotti_validi
        ])

    # Auto-login se ha appena creato un User
    if user_creato:
        auth_login(request, user_creato)

    # Email cliente: usa email fornita nel form (se guest), altrimenti
    # quella del cliente. Logga sempre quale viene usata.
    import logging
    log = logging.getLogger(__name__)
    email_target = (
        email if email
        else (cliente.email if cliente else None)
    )
    log.info(
        'Prenotazione %s creata. Invio email a: %s (cliente.email=%s)',
        prenotazione.codice_prenotazione, email_target, cliente.email,
    )
    notifica_prenotazione_ricevuta(prenotazione, to_email=email_target)

    # Notifica WebSocket realtime agli operatori (gruppo 'ordini_list')
    # con timeout duro per non bloccare la risposta se Redis e' lento.
    from apps.api.notify import notify_group
    notify_group('ordini_list', {
        'type': 'nuova_prenotazione',
        'prenotazione_id': prenotazione.id,
        'codice': prenotazione.codice_prenotazione,
        'cliente': str(cliente),
        'data': data_p.strftime('%d/%m/%Y'),
        'ora': ora_inizio.strftime('%H:%M'),
        'servizi': [s.titolo for s in tutti_servizi],
        'tipo_auto': tipo_auto,
        'timestamp': timezone.now().isoformat(),
    })

    return JsonResponse({
        'ok': True,
        'codice': prenotazione.codice_prenotazione,
        'data': data_p.strftime('%d/%m/%Y'),
        'ora': ora_inizio.strftime('%H:%M'),
        'stato': 'in_attesa',
        'redirect': reverse('clients:dashboard') if (request.user.is_authenticated or user_creato) else reverse('clients:landing'),
    })


@login_required
def test_email(request):
    """Diagnostico: invia un'email di test al destinatario indicato.

    GET /app/api/test-email/?to=tuoemail@dominio.it

    Solo staff. Risponde con dettagli sull'esito (backend usato, errore
    eventuale, configurazione attiva). Utile per verificare la
    configurazione SMTP in produzione.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Solo staff'}, status=403)
    to = request.GET.get('to') or request.user.email
    if not to:
        return JsonResponse({
            'error': 'Specifica ?to=email o assicurati che il tuo user abbia un\'email',
            'config': _email_config_info(),
        }, status=400)
    from django.core.mail import send_mail
    info = _email_config_info()
    try:
        sent = send_mail(
            'Test email da Autolavaggio',
            'Se ricevi questa email la configurazione SMTP funziona correttamente.',
            settings.DEFAULT_FROM_EMAIL,
            [to],
            fail_silently=False,
        )
        return JsonResponse({
            'ok': bool(sent),
            'sent_count': sent,
            'to': to,
            'config': info,
            'note': 'Con backend console (DEBUG=True) le email vanno solo in stdout, non arrivano davvero.',
        })
    except Exception as e:
        return JsonResponse({
            'ok': False,
            'error': str(e),
            'error_type': type(e).__name__,
            'to': to,
            'config': info,
        }, status=500)


def _email_config_info():
    return {
        'EMAIL_BACKEND': getattr(settings, 'EMAIL_BACKEND', None),
        'EMAIL_HOST': getattr(settings, 'EMAIL_HOST', None),
        'EMAIL_PORT': getattr(settings, 'EMAIL_PORT', None),
        'EMAIL_USE_TLS': getattr(settings, 'EMAIL_USE_TLS', None),
        'EMAIL_HOST_USER': getattr(settings, 'EMAIL_HOST_USER', '') or '(vuoto)',
        'EMAIL_HOST_PASSWORD_SET': bool(getattr(settings, 'EMAIL_HOST_PASSWORD', '')),
        'DEFAULT_FROM_EMAIL': getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        'DEBUG': settings.DEBUG,
    }


@login_required
def annulla_prenotazione(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo non permesso'}, status=405)
    if not _is_cliente(request.user):
        return JsonResponse({'error': 'Non autorizzato'}, status=403)
    try:
        p = Prenotazione.objects.get(pk=pk, cliente=request.user.cliente)
    except Prenotazione.DoesNotExist:
        return JsonResponse({'error': 'Prenotazione non trovata'}, status=404)
    if not p.can_be_cancelled:
        return JsonResponse({'error': 'Non e possibile annullare questa prenotazione'}, status=400)
    if hasattr(p, 'annulla'):
        p.annulla('Annullata dal cliente')
    else:
        p.stato = 'annullata'
        p.save(update_fields=['stato'])
    return JsonResponse({'ok': True})
