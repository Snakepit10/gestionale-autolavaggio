"""Avvio di un lavaggio a scalare dal saldo monete.

Sequenza (l'ordine e' deliberato):
1. validazioni (nodo attivo, range impulsi, MQTT configurato);
2. guard anti-raffica: cooldown per cliente+nodo (config singleton);
3. ADDEBITO monete in transazione COMMITTATA prima del MQTT — mai
   impulsi gratis, e mai chiamare MQTT dentro la transazione (dorme
   ~1 s per impulso e terrebbe il lock sulla riga saldo);
4. invio impulsi via apps.impianto.mqtt.moneta_virtuale;
5. se sono partiti meno impulsi del previsto, STORNO automatico delle
   monete corrispondenti agli impulsi non erogati.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.impianto.mqtt import moneta_virtuale, mqtt_configurato
from apps.monete.models import ImpostazioniMonete, MovimentoMoneta, NodoImpianto
from . import wallet

logger = logging.getLogger('apps.monete.lavaggio')


@dataclass
class EsitoAvvio:
    ok: bool
    messaggio: str
    inviati: int = 0
    movimento: MovimentoMoneta | None = None
    storno: MovimentoMoneta | None = None


def avvia_lavaggio(*, cliente, nodo: NodoImpianto, impulsi: int,
                   operatore=None, chiave_idempotenza: str = '',
                   forza: bool = False) -> EsitoAvvio:
    """Scala il saldo del cliente e invia gli impulsi al nodo.

    `chiave_idempotenza`: UUID generato dal form di conferma; un replay
    dello stesso form non produce un secondo addebito.
    `forza`: lo staff puo' scavalcare il cooldown anti-raffica.
    """
    # 1. Validazioni
    if not nodo.attivo:
        return EsitoAvvio(False, f'Il nodo "{nodo.nome}" non e\' attivo.')
    if not 1 <= impulsi <= nodo.max_impulsi:
        return EsitoAvvio(
            False, f'Numero di impulsi non valido (1-{nodo.max_impulsi}).')
    if not mqtt_configurato():
        return EsitoAvvio(False, 'Impianto non raggiungibile (MQTT non configurato).')

    # 2. Cooldown anti-raffica (doppio tap, doppio click, refresh)
    cfg = ImpostazioniMonete.get_solo()
    if not forza and cfg.cooldown_lavaggio_sec:
        soglia = timezone.now() - timedelta(seconds=cfg.cooldown_lavaggio_sec)
        recente = MovimentoMoneta.objects.filter(
            cliente=cliente, tipo='lavaggio', nodo=nodo,
            creato_il__gte=soglia,
        ).exists()
        if recente:
            return EsitoAvvio(
                False,
                f'Hai appena avviato questo nodo: attendi '
                f'{cfg.cooldown_lavaggio_sec} secondi prima di riprovare.')

    costo = nodo.costo_monete(impulsi)

    # 3. Addebito PRIMA degli impulsi (transazione autonoma)
    try:
        movimento = wallet.addebita(
            cliente, costo, 'lavaggio',
            f'Avvio {nodo.nome}: {impulsi} impulso/i',
            nodo=nodo, impulsi=impulsi, operatore=operatore,
            chiave_idempotenza=chiave_idempotenza,
        )
    except wallet.SaldoInsufficienteError as exc:
        return EsitoAvvio(False, str(exc))
    except wallet.OperazioneDuplicataError:
        return EsitoAvvio(
            False, 'Operazione gia\' registrata: gli impulsi sono gia\' '
                   'stati inviati, non serve ripetere.')

    # 4. Impulsi via MQTT (fuori da ogni transazione)
    ok, msg, inviati = moneta_virtuale(
        nodo.slug, impulsi, switch_id=nodo.switch_id)

    # 5. Storno degli impulsi non erogati
    storno = None
    non_erogati = impulsi - inviati
    if non_erogati > 0:
        monete_storno = non_erogati * nodo.monete_per_impulso
        try:
            storno = wallet.accredita(
                cliente, monete_storno, 'storno',
                f'Storno {non_erogati} impulso/i non erogati su {nodo.nome} ({msg})',
                operatore=operatore,
                chiave_idempotenza=(f'{chiave_idempotenza}:storno'
                                    if chiave_idempotenza else ''),
            )
        except Exception:
            # Estremo: anche lo storno (solo DB) e' fallito. Logga tutto
            # per la rettifica manuale dall'admin.
            logger.exception(
                'STORNO FALLITO: cliente=%s nodo=%s monete=%s chiave=%s',
                cliente.pk, nodo.slug, monete_storno, chiave_idempotenza)

    if ok:
        return EsitoAvvio(True,
                          f'{inviati} impulso/i inviati a {nodo.nome} '
                          f'({costo} monete).',
                          inviati, movimento, storno)
    if inviati > 0:
        return EsitoAvvio(False,
                          f'Inviati solo {inviati}/{impulsi} impulsi: '
                          f'stornate le monete rimanenti. ({msg})',
                          inviati, movimento, storno)
    return EsitoAvvio(False,
                      f'Nessun impulso inviato: monete stornate. ({msg})',
                      0, movimento, storno)
