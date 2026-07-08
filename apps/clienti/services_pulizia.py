"""Pulizia anagrafica clienti: rilevamento duplicati per numero di
telefono, unione (merge) e gestione dei clienti senza recapito.

Merge design:
- Il chiamante sceglie il cliente MASTER; gli altri del gruppo vengono
  fusi dentro di lui e poi cancellati.
- Le relazioni inverse vengono riassegnate in modo GENERICO iterando
  Cliente._meta.related_objects: qualsiasi modello futuro con FK a
  Cliente viene riassegnato senza toccare questo file.
- Casi speciali:
  - PuntiFedelta (OneToOne): i punti si sommano sul master.
  - user (OneToOne diretto su Cliente): passa al master solo se il
    master non ha gia' un account.
  - Vincoli unique (es. InvioCampagna unique per campagna+cliente):
    la riga del duplicato che confligge viene eliminata (il master ha
    gia' la sua).
- I campi anagrafici vuoti del master vengono completati con quelli
  del duplicato (mai sovrascritti).
- blocca_marketing/consenso: OR (se uno dei profili aveva opt-out,
  il master eredita l'opt-out).
"""
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Count

from .models import Cliente, PuntiFedelta
from .utils import normalizza_telefono


@dataclass
class ClienteInfo:
    cliente: Cliente
    n_ordini: int
    n_prenotazioni: int
    ha_account: bool

    @property
    def eliminabile(self) -> bool:
        """Anagrafica 'vuota': nessuna attivita' collegata."""
        return (self.n_ordini == 0 and self.n_prenotazioni == 0
                and not self.ha_account
                and not self.cliente.abbonamenti.exists())


def _info(c: Cliente) -> ClienteInfo:
    return ClienteInfo(
        cliente=c,
        n_ordini=c.ordine_set.count(),
        n_prenotazioni=c.prenotazioni.count(),
        ha_account=bool(c.user_id),
    )


def trova_duplicati() -> list[list[ClienteInfo]]:
    """Gruppi di clienti che condividono lo stesso numero (normalizzato).

    Ritorna una lista di gruppi, ciascuno ordinato per "ricchezza"
    (piu' ordini prima): il primo del gruppo e' il candidato master
    naturale.
    """
    per_numero: dict[str, list[Cliente]] = {}
    for c in Cliente.objects.exclude(telefono=''):
        e164 = normalizza_telefono(c.telefono)
        if not e164:
            continue
        per_numero.setdefault(e164, []).append(c)

    gruppi = []
    for numero, clienti in per_numero.items():
        if len(clienti) < 2:
            continue
        infos = [_info(c) for c in clienti]
        infos.sort(key=lambda i: (-i.n_ordini, -i.n_prenotazioni,
                                  not i.ha_account, i.cliente.pk))
        gruppi.append(infos)
    # Gruppi piu' grossi prima
    gruppi.sort(key=lambda g: -len(g))
    return gruppi


def clienti_senza_telefono() -> list[ClienteInfo]:
    """Clienti senza numero o con numero non parsabile."""
    out = []
    for c in Cliente.objects.all():
        if not (c.telefono or '').strip() or not normalizza_telefono(c.telefono):
            out.append(_info(c))
    out.sort(key=lambda i: (i.eliminabile is False, -i.n_ordini))
    return out


def _riassegna_generico(dup: Cliente, master: Cliente) -> None:
    """Riassegna al master tutte le righe che puntano al duplicato.

    Iterazione generica su related_objects: copre Ordine, Prenotazione,
    MovimentoPunti, ConversazioneWhatsApp, InvioCampagna, Abbonamento e
    qualunque FK futura senza doverla elencare. Le OneToOne sono escluse
    (gestite ad hoc dal chiamante).
    """
    for rel in Cliente._meta.related_objects:
        if rel.one_to_one:
            continue
        model = rel.related_model
        campo = rel.field.name
        qs = model.objects.filter(**{campo: dup})
        try:
            with transaction.atomic():
                qs.update(**{campo: master})
        except IntegrityError:
            # Vincolo unique che coinvolge cliente (es. InvioCampagna
            # unique per campagna+cliente): riassegna riga per riga e
            # scarta quelle che confliggono col master.
            for row in model.objects.filter(**{campo: dup}):
                try:
                    with transaction.atomic():
                        setattr(row, campo, master)
                        row.save(update_fields=[campo])
                except IntegrityError:
                    row.delete()


_CAMPI_COMPLETABILI = [
    'email', 'indirizzo', 'cap', 'citta', 'nome', 'cognome',
    'codice_fiscale', 'ragione_sociale', 'partita_iva', 'codice_sdi', 'pec',
]


@transaction.atomic
def unisci_clienti(master: Cliente, duplicati: list[Cliente]) -> int:
    """Fonde i duplicati nel master e li elimina. Ritorna quanti fusi."""
    fusi = 0
    for dup in duplicati:
        if dup.pk == master.pk:
            continue

        _riassegna_generico(dup, master)

        # PuntiFedelta: somma nel master
        try:
            pf_dup = dup.punti_fedelta
        except PuntiFedelta.DoesNotExist:
            pf_dup = None
        if pf_dup:
            pf_master, _ = PuntiFedelta.objects.get_or_create(cliente=master)
            pf_master.punti_totali += pf_dup.punti_totali
            pf_master.punti_utilizzati += pf_dup.punti_utilizzati
            pf_master.save()
            pf_dup.delete()

        # Account online: al master solo se non ne ha gia' uno
        if dup.user_id and not master.user_id:
            master.user = dup.user
            dup.user = None
            dup.save(update_fields=['user'])

        # Completa i campi vuoti del master
        campi_aggiornati = []
        for campo in _CAMPI_COMPLETABILI:
            if not getattr(master, campo) and getattr(dup, campo):
                setattr(master, campo, getattr(dup, campo))
                campi_aggiornati.append(campo)

        # Flag marketing: OR conservativo (opt-out vince sempre)
        if dup.blocca_marketing and not master.blocca_marketing:
            master.blocca_marketing = True
            master.blocca_marketing_il = dup.blocca_marketing_il
            master.blocca_marketing_motivo = dup.blocca_marketing_motivo
            campi_aggiornati += ['blocca_marketing', 'blocca_marketing_il',
                                 'blocca_marketing_motivo']
        if dup.consenso_marketing and not master.consenso_marketing:
            master.consenso_marketing = True
            campi_aggiornati.append('consenso_marketing')

        if master.user_id:
            campi_aggiornati.append('user')
        if campi_aggiornati:
            master.save()

        dup.delete()
        fusi += 1
    return fusi


def elimina_clienti_vuoti(cliente_ids: list[int]) -> tuple[int, int]:
    """Elimina i clienti indicati SOLO se ancora 'vuoti' (ri-verifica
    server-side). Ritorna (eliminati, rifiutati)."""
    eliminati = rifiutati = 0
    for c in Cliente.objects.filter(pk__in=cliente_ids):
        if _info(c).eliminabile:
            c.delete()
            eliminati += 1
        else:
            rifiutati += 1
    return eliminati, rifiutati
