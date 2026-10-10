"""Sezioni del gestionale e permessi di accesso per gruppo / utente.

Una sezione corrisponde a una voce del menu ed e' individuata dai prefissi
dei suoi indirizzi: vince il prefisso piu' lungo (es. /ordini/cassa/ e'
la Cassa, il resto di /ordini/ gli Ordini). La regola di accesso e' una
sola, usata da menu, middleware, viste e WebSocket:

1. superuser -> sempre;
2. eccezione per l'utente sulla sezione -> vale quella (consenti/nega);
3. altrimenti -> se uno dei suoi gruppi ha la sezione spuntata.

La matrice si modifica dalla pagina Configurazione > Permessi.
"""

# (chiave, nome, descrizione, prefissi)
SEZIONI = [
    ('clienti', 'Clienti', 'Anagrafica clienti e garage', ('/clienti/', '/garage/')),
    ('messaggi', 'Messaggi WhatsApp', 'Inbox WhatsApp e invio messaggi',
     ('/messaggi/', '/api/whatsapp/conversazioni/', '/api/whatsapp/media/')),
    ('cassa', 'Cassa', 'Punto cassa', ('/ordini/cassa/',)),
    ('ordini', 'Ordini', 'Ordini, vendita prodotti, non pagati', ('/ordini/',)),
    ('task', 'Task', 'Attività e progetti', ('/tasks/',)),
    ('marketing', 'Marketing', 'Campagne, segmenti, statistiche', ('/marketing/',)),
    ('fatture', 'Fatture', 'Fatturazione', ('/fatture/',)),
    ('abbonamenti', 'Abbonamenti', 'Abbonamenti e vendita', ('/abbonamenti/',)),
    ('prenotazioni', 'Prenotazioni', 'Check-in, calendario, nuove prenotazioni', ('/prenotazioni/',)),
    ('qualita', 'Qualità', 'Controllo qualità e punteggi', ('/cq/',)),
    ('mio_turno', 'Il Mio Turno', 'Turno operatore: postazioni, checklist, lavorazioni, la mia dotazione',
     ('/turni/', '/magazzino/mia-dotazione/')),
    ('report_turni', 'Turni (report)', 'Report tempi e storico checklist',
     ('/turni/report/', '/turni/api/report/', '/turni/storico-checklist/', '/turni/api/verifica/')),
    ('magazzino', 'Magazzino', 'Articoli, fornitori, ordini, consegne, assegnazioni a operatori e postazioni',
     ('/magazzino/', '/scorte/')),
    ('finanze', 'Finanze', 'Report giornata/periodo, saldi, quadrature', ('/finanze/',)),
    ('monete', 'Monete', 'Gestione monete digitali', ('/monete/',)),
    ('configurazione', 'Configurazione', 'Catalogo, categorie, postazioni, checklist, slot, abbonamenti...',
     ('/categorie/', '/catalogo/', '/sconti/', '/stampanti/', '/postazioni/',
      '/cartellini/', '/cq/configurazione/', '/turni/configurazione/',
      '/turni/api/checklist-item/', '/turni/api/categoria-checklist/', '/turni/api/esito-checklist/',
      '/prenotazioni/configurazione/', '/abbonamenti/configurazioni/')),
    ('permessi', 'Permessi', 'Questa pagina: solo titolare e amministratore', ('/permessi/',)),
]

NOMI = {chiave: nome for chiave, nome, _, _ in SEZIONI}
# Sezioni la cui matrice non si modifica (per non chiudersi fuori)
FISSE = {'permessi': ('titolare',)}
GRUPPI = ('titolare', 'responsabile', 'operatore')

# Mai sotto permesso di sezione: pagine cliente, chioschi, webhook
ESCLUSI = (
    '/clienti/area-cliente/', '/prenotazioni/prenota/', '/abbonamenti/shop/',
    '/abbonamenti/verifica/', '/abbonamenti/api/verifica/',
    '/api/whatsapp/webhook/', '/monete/webhook/', '/api/servizi/',
)
# Servizi usati in background da piu' sezioni (es. la cassa cerca i
# clienti, il menu legge il contatore dei task): basta essere operatori
CONDIVISI = (
    '/clienti/cerca/', '/clienti/api/', '/ordini/api/', '/prenotazioni/api/',
    '/tasks/api/conteggio/', '/magazzino/foto/',
)

_PREFISSI = sorted(((p, chiave) for chiave, _, _, prefissi in SEZIONI for p in prefissi),
                   key=lambda x: -len(x[0]))


def sezione_per_percorso(path):
    """Chiave della sezione dell'indirizzo, o None se libero."""
    if any(path.startswith(p) for p in ESCLUSI + CONDIVISI):
        return None
    for prefisso, chiave in _PREFISSI:
        if path.startswith(prefisso):
            return chiave
    return None


def sezioni_permesse(user):
    """Insieme delle chiavi accessibili all'utente (cache sull'oggetto)."""
    if not getattr(user, 'is_authenticated', False):
        return set()
    cache = getattr(user, '_sezioni_permesse', None)
    if cache is not None:
        return cache
    if user.is_superuser:
        permesse = {chiave for chiave, _, _, _ in SEZIONI}
    else:
        from .models import PermessoGruppo, PermessoUtente

        gruppi = set(user.groups.values_list('name', flat=True))
        permesse = set(PermessoGruppo.objects.filter(gruppo__name__in=gruppi)
                       .values_list('sezione', flat=True))
        for sezione, gruppi_fissi in FISSE.items():
            permesse.discard(sezione)
            if gruppi & set(gruppi_fissi):
                permesse.add(sezione)
        for sezione, consenti in PermessoUtente.objects.filter(user=user).values_list('sezione', 'consenti'):
            if sezione in FISSE:
                continue
            (permesse.add if consenti else permesse.discard)(sezione)
    user._sezioni_permesse = permesse
    return permesse


def ha_accesso(user, sezione):
    return sezione in sezioni_permesse(user)
