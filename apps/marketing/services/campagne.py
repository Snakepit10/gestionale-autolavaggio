"""Logica di preparazione e creazione campagne marketing.

Separata dalle viste per essere riusabile dal cron (F4/F5): il
controllo di eleggibilita' viene rifatto anche al momento dell'invio
(l'opt-out puo' arrivare tra il lancio e l'invio scaglionato).
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone

from apps.clienti.models import Cliente
from apps.marketing.models import Campagna, ImpostazioniMarketing, InvioCampagna

# Placeholder supportati nei parametri template. La resolve avviene
# per-cliente sia in preview che al momento dell'invio (dati freschi).
PLACEHOLDER_SUPPORTATI = ('{nome}', '{giorni_ultimo_lavaggio}', '{totale_lavaggi}')


def dati_placeholder(cliente: Cliente) -> dict:
    """Valori correnti dei placeholder per un cliente."""
    stats = (
        cliente.ordine_set.filter(stato='completato')
        .aggregate(n=Count('id'), ultimo=Max('data_ora'))
    )
    giorni = (timezone.now() - stats['ultimo']).days if stats['ultimo'] else ''
    nome = (cliente.nome or cliente.ragione_sociale or '').strip() or 'Cliente'
    return {
        '{nome}': nome,
        '{giorni_ultimo_lavaggio}': str(giorni),
        '{totale_lavaggi}': str(stats['n'] or 0),
    }


def risolvi_params(template_params: list, cliente: Cliente) -> list[str]:
    """Sostituisce i placeholder nei parametri template per un cliente."""
    dati = dati_placeholder(cliente)
    out = []
    for p in template_params:
        testo = str(p)
        for ph, val in dati.items():
            testo = testo.replace(ph, val)
        out.append(testo)
    return out


@dataclass
class EsitoEleggibilita:
    eleggibile: bool
    motivo: str = ''


def verifica_eleggibilita(cliente: Cliente,
                          cfg: ImpostazioniMarketing | None = None,
                          escludi_campagna: Campagna | None = None) -> EsitoEleggibilita:
    """Un cliente e' contattabile per una campagna promozionale?

    Regole (in ordine):
    1. telefono presente
    2. niente opt-out (blocca_marketing)
    3. non contattato negli ultimi finestra_no_ricontatto_giorni
    4. non gia' in coda in un'altra campagna attiva

    `escludi_campagna`: al re-check in fase di invio, la campagna
    corrente non deve auto-escludersi (il suo stesso invio in_coda
    farebbe scattare la regola 4).
    """
    cfg = cfg or ImpostazioniMarketing.get_solo()

    if not (cliente.telefono or '').strip():
        return EsitoEleggibilita(False, 'telefono mancante')
    if cliente.blocca_marketing:
        return EsitoEleggibilita(False, 'opt-out (non contattare)')

    da = timezone.now() - timedelta(days=cfg.finestra_no_ricontatto_giorni)
    if InvioCampagna.objects.filter(
        cliente=cliente, stato='inviato', inviato_il__gte=da,
    ).exists():
        return EsitoEleggibilita(
            False, f'contattato negli ultimi {cfg.finestra_no_ricontatto_giorni} giorni')

    in_coda = InvioCampagna.objects.filter(
        cliente=cliente, stato='in_coda',
        campagna__stato__in=['in_coda', 'in_corso'],
    )
    if escludi_campagna is not None:
        in_coda = in_coda.exclude(campagna=escludi_campagna)
    if in_coda.exists():
        return EsitoEleggibilita(False, "gia' in un'altra campagna attiva")

    return EsitoEleggibilita(True)


def prepara_destinatari(cliente_ids: list[int]) -> tuple[list, list]:
    """Divide i clienti in (eleggibili, esclusi_con_motivo).

    Ritorna ([Cliente], [(Cliente, motivo)]).
    """
    cfg = ImpostazioniMarketing.get_solo()
    eleggibili, esclusi = [], []
    for c in Cliente.objects.filter(pk__in=cliente_ids):
        esito = verifica_eleggibilita(c, cfg)
        if esito.eleggibile:
            eleggibili.append(c)
        else:
            esclusi.append((c, esito.motivo))
    return eleggibili, esclusi


def crea_campagna(nome: str, template_meta: str, template_params: list,
                  cliente_ids_selezionati: list[int], segmento: str,
                  user, flusso_continuo: bool = False,
                  riaggancio_giorni: int = 0) -> Campagna:
    """Crea la campagna con i suoi invii.

    - Per i selezionati eleggibili: InvioCampagna stato='in_coda'.
    - Per i selezionati NON eleggibili: InvioCampagna stato='saltato'
      con motivo (tracciabilita': si vede in dashboard chi e' stato
      escluso e perche').
    Il vero invio lo fa il cron esegui_campagne_marketing. Se
    flusso_continuo=True, lo stesso cron continuera' ad accodare i
    nuovi clienti che entrano nei segmenti (aggiorna_flussi_continui).
    """
    cfg = ImpostazioniMarketing.get_solo()
    campagna = Campagna.objects.create(
        nome=nome,
        tipo='manuale',
        stato='in_coda',
        template_meta=template_meta,
        template_params=template_params,
        segmento_origine=segmento,
        flusso_continuo=flusso_continuo,
        riaggancio_giorni=riaggancio_giorni if flusso_continuo else 0,
        finestra_conversione_giorni=cfg.giorni_finestra_conversione,
        creata_da=user,
        lanciata_il=timezone.now(),
    )
    invii = []
    for c in Cliente.objects.filter(pk__in=cliente_ids_selezionati):
        esito = verifica_eleggibilita(c, cfg, escludi_campagna=campagna)
        invii.append(InvioCampagna(
            campagna=campagna,
            cliente=c,
            stato='in_coda' if esito.eleggibile else 'saltato',
            motivo_salto='' if esito.eleggibile else esito.motivo,
        ))
    InvioCampagna.objects.bulk_create(invii)
    return campagna


def aggiorna_flussi_continui(log=None, dry: bool = False) -> dict:
    """Rivaluta i segmenti delle campagne a flusso continuo e accoda i
    clienti nuovi. Chiamata dal cron a ogni run.

    Per ogni campagna flusso_continuo in_coda/in_corso (le campagne in
    pausa o annullate NON vengono toccate):
    - clienti nel segmento SENZA invio -> nuovo InvioCampagna
      (in_coda o saltato con motivo);
    - invii 'saltato' per motivi temporanei (no-ricontatto, altra
      campagna attiva) -> rimessi in coda appena tornano eleggibili;
    - invii 'inviato' piu' vecchi di riaggancio_giorni (se > 0) e
      cliente ancora/di nuovo nel segmento -> rimessi in coda
      (ricontatto ciclico, es. richiamo dormienti).
    """
    log = log or (lambda m: None)
    cfg = ImpostazioniMarketing.get_solo()
    ora = timezone.now()
    tot = {'nuovi': 0, 'ritentati': 0, 'riagganci': 0}

    campagne = Campagna.objects.filter(
        flusso_continuo=True, stato__in=('in_coda', 'in_corso'))
    for campagna in campagne:
        # Import locale: views importa questo modulo, evita il ciclo.
        from apps.marketing.views import (_chiavi_segmenti_valide,
                                          _risolvi_destinatari_da_segmenti)
        chiavi = _chiavi_segmenti_valide(
            [s for s in campagna.segmento_origine.split(',') if s])
        if not chiavi:
            log(f'Flusso "{campagna.nome}": nessun segmento valido '
                f'(eliminato?), skip.')
            continue

        ids_segmento = set(_risolvi_destinatari_da_segmenti(chiavi))
        esistenti = {i.cliente_id: i for i in campagna.invii.all()}
        nuovi = ritentati = riagganci = 0

        for cid in ids_segmento:
            invio = esistenti.get(cid)

            if invio is None:
                cliente = Cliente.objects.filter(pk=cid).first()
                if cliente is None:
                    continue
                esito = verifica_eleggibilita(cliente, cfg,
                                              escludi_campagna=campagna)
                if dry:
                    log(f'[dry-run] flusso "{campagna.nome}": {cliente} -> '
                        f'{"in_coda" if esito.eleggibile else "saltato: " + esito.motivo}')
                    continue
                InvioCampagna.objects.create(
                    campagna=campagna, cliente=cliente,
                    stato='in_coda' if esito.eleggibile else 'saltato',
                    motivo_salto='' if esito.eleggibile else esito.motivo,
                )
                nuovi += 1
                continue

            # Saltato per un motivo temporaneo: ritenta appena eleggibile.
            # L'opt-out resta bloccato dalla verifica stessa.
            if invio.stato == 'saltato':
                esito = verifica_eleggibilita(invio.cliente, cfg,
                                              escludi_campagna=campagna)
                if esito.eleggibile and not dry:
                    InvioCampagna.objects.filter(pk=invio.pk).update(
                        stato='in_coda', inviato_il=None, motivo_salto='')
                    ritentati += 1
                continue

            # Riaggancio ciclico: gia' inviato, ma abbastanza tempo fa.
            if (campagna.riaggancio_giorni and invio.stato == 'inviato'
                    and invio.inviato_il is not None
                    and invio.inviato_il <= ora - timedelta(
                        days=campagna.riaggancio_giorni)):
                esito = verifica_eleggibilita(invio.cliente, cfg,
                                              escludi_campagna=campagna)
                if esito.eleggibile and not dry:
                    InvioCampagna.objects.filter(pk=invio.pk).update(
                        stato='in_coda', inviato_il=None, motivo_salto='')
                    riagganci += 1

        if nuovi or ritentati or riagganci:
            log(f'Flusso "{campagna.nome}": +{nuovi} nuovi, '
                f'{ritentati} ritentati, {riagganci} riagganci.')
        tot['nuovi'] += nuovi
        tot['ritentati'] += ritentati
        tot['riagganci'] += riagganci
    return tot
