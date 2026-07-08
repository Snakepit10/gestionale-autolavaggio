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
                  user) -> Campagna:
    """Crea la campagna con i suoi invii.

    - Per i selezionati eleggibili: InvioCampagna stato='in_coda'.
    - Per i selezionati NON eleggibili: InvioCampagna stato='saltato'
      con motivo (tracciabilita': si vede in dashboard chi e' stato
      escluso e perche').
    Il vero invio lo fa il cron esegui_campagne_marketing.
    """
    cfg = ImpostazioniMarketing.get_solo()
    campagna = Campagna.objects.create(
        nome=nome,
        tipo='manuale',
        stato='in_coda',
        template_meta=template_meta,
        template_params=template_params,
        segmento_origine=segmento,
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
