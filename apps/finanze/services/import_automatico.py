"""Import automatico delle transazioni WashTec Plus.

WashTec Plus non ha un'API pubblica (la pagina parla GWT-RPC): un
Chromium headless (Playwright) fa il login e lancia nella pagina la
stessa estrazione del preferito (static/js/washtec_estrazione.js), poi
le righe passano da import_washtec.importa come nell'import manuale.

Gira nel worker `python manage.py importa_washtec_auto --ciclo`: un giro
ogni WASHTEC_INTERVALLO_MIN minuti in orario di apertura, e subito
quando qualcuno preme "Aggiorna ora" (riga EsecuzioneImportWashtec
'richiesta').
"""
import os
import time as orologio
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.finanze.models import EsecuzioneImportWashtec, TransazionePortale
from apps.finanze.services import import_washtec

URL_WASHTEC = 'https://plus.washtec.com'
SCRIPT_ESTRAZIONE = Path(settings.BASE_DIR) / 'static' / 'js' / 'washtec_estrazione.js'
# Un giro 'in_corso' piu' vecchio di cosi' e' di un worker morto
DURATA_MAX_GIRO = timedelta(minutes=15)
# Una richiesta "Aggiorna ora" non presa entro questo tempo: worker spento
ATTESA_MAX_RICHIESTA = timedelta(minutes=3)
# Oltre questo ritardo dall'ultimo import riuscito lo stato diventa rosso
RITARDO_ALLARME = timedelta(minutes=30)
# Esecuzioni tenute in archivio
DA_TENERE = 200


def _intero_env(nome, predefinito):
    try:
        return int(os.environ.get(nome, predefinito))
    except ValueError:
        return predefinito


def intervallo():
    return timedelta(minutes=_intero_env('WASHTEC_INTERVALLO_MIN', 10))


def in_orario(adesso=None):
    """Orario di apertura (ora locale) in cui girano gli import programmati."""
    ora = timezone.localtime(adesso or timezone.now()).hour
    return _intero_env('WASHTEC_ORA_INIZIO', 7) <= ora < _intero_env('WASHTEC_ORA_FINE', 22)


def periodo(oggi=None):
    """(dal, al): dal giorno prima dell'ultima transazione in archivio
    (almeno da ieri) fino a oggi. I doppioni sono ignorati, quindi
    sovrapporsi non costa nulla e un buco si richiude da solo."""
    oggi = oggi or timezone.localdate()
    ultima = TransazionePortale.tutte.order_by('-orario').first()
    if ultima is None:
        return oggi - timedelta(days=3), oggi
    dal = min(timezone.localtime(ultima.orario).date(), oggi) - timedelta(days=1)
    return min(dal, oggi - timedelta(days=1)), oggi


def richiedi(utente=None):
    """Tasto "Aggiorna ora": una sola richiesta alla volta. Ritorna
    l'esecuzione in attesa (nuova o gia' esistente)."""
    with transaction.atomic():
        _chiudi_giri_morti()
        aperta = (EsecuzioneImportWashtec.objects.select_for_update()
                  .filter(esito__in=('richiesta', 'in_corso')).first())
        if aperta:
            return aperta
        return EsecuzioneImportWashtec.objects.create(
            esito='richiesta', richiesta_da=utente if utente and utente.is_authenticated else None)


def _chiudi_giri_morti():
    adesso = timezone.now()
    EsecuzioneImportWashtec.objects.filter(esito='in_corso', avviata_il__lt=adesso - DURATA_MAX_GIRO).update(
        esito='errore', finita_il=adesso, messaggio='Interrotto: il servizio di aggiornamento non ha finito il giro.')
    # il worker controlla ogni ~15 secondi: una richiesta mai presa vuol
    # dire che il servizio e' spento
    EsecuzioneImportWashtec.objects.filter(esito='richiesta', creata_il__lt=adesso - ATTESA_MAX_RICHIESTA).update(
        esito='errore', finita_il=adesso,
        messaggio='Il servizio di aggiornamento WashTec non risponde: e\' acceso su Railway?')


def stato():
    """Per le pagine: ultimo import riuscito, eventuale giro in attesa o
    in corso, ultimo errore e se segnalare in rosso."""
    _chiudi_giri_morti()
    ultima_ok = EsecuzioneImportWashtec.objects.filter(esito='ok').order_by('-finita_il').first()
    ultima = EsecuzioneImportWashtec.objects.filter(esito__in=('ok', 'errore')).order_by('-finita_il').first()
    in_attesa = EsecuzioneImportWashtec.objects.filter(esito__in=('richiesta', 'in_corso')).first()
    adesso = timezone.now()
    errore = ultima if ultima and ultima.esito == 'errore' else None
    in_ritardo = in_orario(adesso) and (ultima_ok is None or adesso - ultima_ok.finita_il > RITARDO_ALLARME)
    return {
        'ultima_ok': ultima_ok,
        'errore': errore,
        'in_attesa': in_attesa,
        'in_ritardo': in_ritardo,
        # finche' l'import automatico non e' mai partito non si allarma
        'allarme': bool(errore or (in_ritardo and ultima_ok)),
        'ultima_transazione': TransazionePortale.tutte.order_by('-orario').first(),
    }


def stato_json():
    s = stato()
    fmt = lambda dt: timezone.localtime(dt).strftime('%d/%m %H:%M') if dt else None
    return {
        'in_attesa': bool(s['in_attesa']),
        'in_corso': bool(s['in_attesa'] and s['in_attesa'].esito == 'in_corso'),
        'ultima_ok': fmt(s['ultima_ok'].finita_il) if s['ultima_ok'] else None,
        'nuove': s['ultima_ok'].nuove if s['ultima_ok'] else 0,
        'errore': s['errore'].messaggio if s['errore'] else '',
        'allarme': s['allarme'],
    }


def esegui(esecuzione=None, estrai=None, dal=None, al=None, conferma=True):
    """Un giro di import. `estrai(dal, al)` ritorna le righe grezze
    (predefinito: Playwright su WashTec Plus). Non lancia eccezioni:
    l'esito resta sull'EsecuzioneImportWashtec."""
    if esecuzione is None:
        esecuzione = EsecuzioneImportWashtec.objects.create(esito='in_corso')
    p_dal, p_al = periodo()
    esecuzione.dal, esecuzione.al = dal or p_dal, al or p_al
    esecuzione.esito = 'in_corso'
    esecuzione.avviata_il = timezone.now()
    esecuzione.save()
    try:
        righe = (estrai or estrai_playwright)(esecuzione.dal, esecuzione.al)
        esito = import_washtec.importa(righe, conferma=conferma)
        esecuzione.righe = esito['righe']
        esecuzione.nuove = esito['nuove']
        esecuzione.messaggio = '\n'.join(esito['anomalie'])
        esecuzione.esito = 'ok'
    except Exception as e:                      # noqa: BLE001 - l'errore va mostrato, non perso
        esecuzione.esito = 'errore'
        esecuzione.messaggio = str(e)[:2000] or e.__class__.__name__
    esecuzione.finita_il = timezone.now()
    esecuzione.save()
    _pulisci()
    return esecuzione


def _pulisci():
    vecchie = EsecuzioneImportWashtec.objects.order_by('-creata_il').values_list('pk', flat=True)[DA_TENERE:]
    EsecuzioneImportWashtec.objects.filter(pk__in=list(vecchie)).delete()


def estrai_playwright(dal, al, visibile=False):
    """Login su WashTec Plus e estrazione delle righe dal `dal` al `al`."""
    from playwright.sync_api import sync_playwright

    utente = os.environ.get('WASHTEC_UTENTE')
    password = os.environ.get('WASHTEC_PASSWORD')
    if not utente or not password:
        raise RuntimeError('Credenziali mancanti: imposta WASHTEC_UTENTE e WASHTEC_PASSWORD.')
    script = SCRIPT_ESTRAZIONE.read_text(encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visibile)
        try:
            # WashTec mostra gli orari nel fuso del browser: deve essere il
            # nostro, non quello (UTC) del server. Interfaccia in italiano,
            # perche' l'estrazione legge i testi ("Dati transazione", "di").
            contesto = browser.new_context(locale='it-IT', timezone_id=settings.TIME_ZONE,
                                           viewport={'width': 1400, 'height': 1000})
            pagina = contesto.new_page()
            pagina.set_default_timeout(60000)
            pagina.goto(URL_WASHTEC)
            _login(pagina, utente, password)
            return pagina.evaluate(
                '([dal, al]) => { ' + script + '\n return estraiWashtec(dal, al); }',
                [dal.strftime('%d/%m/%y'), al.strftime('%d/%m/%y')])
        except Exception as e:
            # le eccezioni JS arrivano come "Error: ... stringa": tengo la stringa
            raise RuntimeError(str(e).splitlines()[0][:500]) from e
        finally:
            browser.close()


def _login(pagina, utente, password):
    campo_password = pagina.locator('input[type=password]:visible').first
    campo_password.wait_for()
    campo_utente = pagina.locator(
        'input[type=text]:visible, input[type=email]:visible, input:not([type]):visible').first
    campo_utente.fill(utente)
    campo_password.fill(password)
    campo_password.press('Enter')
    try:
        campo_password.wait_for(state='hidden', timeout=30000)
    except Exception:
        raise RuntimeError('Login WashTec non riuscito: controlla WASHTEC_UTENTE e WASHTEC_PASSWORD.')


def ciclo(attesa=15, estrai=None, giri=None, scrivi=print):
    """Worker: ogni `attesa` secondi guarda se c'e' una richiesta e, in
    orario, fa comunque un giro ogni intervallo(). `giri` limita i
    controlli (per i test)."""
    ultimo_programmato = None
    n = 0
    while giri is None or n < giri:
        n += 1
        _chiudi_giri_morti()
        richiesta = EsecuzioneImportWashtec.objects.filter(esito='richiesta').order_by('creata_il').first()
        adesso = timezone.now()
        dovuto = in_orario(adesso) and (ultimo_programmato is None
                                        or adesso - ultimo_programmato >= intervallo())
        if richiesta or dovuto:
            if not richiesta:
                ultimo_programmato = adesso
            e = esegui(richiesta, estrai=estrai)
            scrivi(f'{timezone.localtime(e.finita_il):%d/%m %H:%M:%S} import WashTec {e.esito}: '
                   f'{e.righe} righe, {e.nuove} nuove {e.messaggio[:200]}'.strip())
        if giri is None or n < giri:
            orologio.sleep(attesa)

