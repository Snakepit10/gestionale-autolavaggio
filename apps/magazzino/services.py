"""Operazioni di magazzino: ogni variazione di quantita' passa da qui."""
from io import BytesIO

from django.db import transaction
from django.utils import timezone

from .models import Articolo, Assegnazione, FotoArticolo, Movimento, StatoAssegnazione

FOTO_LATO_MAX = 1000          # px
FOTO_PESO_MAX = 20 * 1024 * 1024

# Esiti della categoria checklist "Dotazione" -> stato dell'assegnazione
CATEGORIA_DOTAZIONE = 'Dotazione magazzino'
ESITI_DOTAZIONE = [
    ('ok', 'OK', 'success', 1),
    ('usurato', 'Usurato', 'warning', 2),
    ('rotto', 'Rotto', 'danger', 3),
    ('mancante', 'Mancante', 'danger', 4),
]
ESITO_A_STATO = {'usurato': 'usurato', 'rotto': 'rotto', 'mancante': 'smarrito'}


# ---------------------------------------------------------------------------
# Quantita'
# ---------------------------------------------------------------------------

def sincronizza_prodotto(articolo):
    """Copia quantita', soglia e codice nel prodotto del catalogo collegato
    (-1 = illimitato), letto da cassa, ordini e catalogo."""
    if not articolo.prodotto_id:
        return
    from apps.core.models import ServizioProdotto

    # la quantita' puo' essere cambiata da un movimento su un'altra copia
    articolo.refresh_from_db(fields=['quantita'])

    ServizioProdotto.objects.filter(pk=articolo.prodotto_id).update(
        quantita_disponibile=max(articolo.quantita, 0) if articolo.traccia_scorte else -1,
        quantita_minima_alert=articolo.scorta_minima,
        codice_prodotto=articolo.codice,
    )


@transaction.atomic
def movimenta(articolo, delta, tipo, operatore=None, nota='', ordine=None,
              consegna=None, assegnazione=None):
    """Registra il movimento e aggiorna la quantita' (che puo' andare
    sotto zero: segnala un inventario da rettificare). Per gli articoli
    non tracciati il movimento resta nel registro ma la quantita' non
    cambia."""
    articolo = Articolo.objects.select_for_update().get(pk=articolo.pk)
    prima = articolo.quantita
    if articolo.traccia_scorte:
        articolo.quantita = prima + delta
        articolo.save(update_fields=['quantita'])
    mov = Movimento.objects.create(
        articolo=articolo, tipo=tipo, quantita=delta, quantita_prima=prima,
        quantita_dopo=articolo.quantita, operatore=operatore, nota=nota[:255],
        ordine=ordine, consegna=consegna, assegnazione=assegnazione)
    sincronizza_prodotto(articolo)
    return mov


def rettifica(articolo, quantita_contata, operatore=None, nota=''):
    """Inventario: porta la quantita' al valore contato."""
    articolo.refresh_from_db(fields=['quantita'])
    delta = quantita_contata - articolo.quantita
    return movimenta(articolo, delta, 'rettifica', operatore=operatore,
                     nota=nota or f'Inventario: contati {quantita_contata}')


def articolo_del_prodotto(prodotto):
    """Articolo collegato a un prodotto del catalogo; lo crea se manca.
    Niente articolo per i prodotti che non sono merce (categorie "senza
    magazzino", es. ricariche credito)."""
    if prodotto.tipo != 'prodotto':
        return None
    articolo = Articolo.objects.filter(prodotto=prodotto).first()
    if articolo is None and prodotto.categoria.senza_magazzino:
        return None
    if articolo is None:
        q = prodotto.quantita_disponibile
        articolo = Articolo.objects.create(
            nome=prodotto.titolo, tipo='vendita', codice=prodotto.codice_prodotto or '',
            categoria=prodotto.gruppo or '', quantita=max(q, 0), traccia_scorte=q >= 0,
            scorta_minima=prodotto.quantita_minima_alert if q >= 0 else 0,
            prodotto=prodotto)
    return articolo


def sincronizza_catalogo(articolo, prezzo=None, categoria=None):
    """Gli articoli in vendita e i prodotti del catalogo coincidono.

    Articolo 'vendita' attivo: crea il prodotto se manca (servono prezzo e
    categoria del catalogo) e ne allinea titolo, sottocategoria in cassa
    (gruppo), codice, prezzo e quantita'. Altrimenti il prodotto collegato
    viene disattivato in cassa (resta nel catalogo con lo storico)."""
    from apps.core.models import ServizioProdotto

    prodotto = articolo.prodotto
    in_vendita = articolo.tipo == 'vendita' and articolo.attivo
    if not in_vendita:
        if prodotto and prodotto.attivo:
            prodotto.attivo = False
            prodotto._da_magazzino = True
            prodotto.save()
        return prodotto
    if prodotto is None:
        if prezzo is None or categoria is None:
            raise ValueError('Per mettere in vendita servono prezzo e categoria in cassa')
        prodotto = ServizioProdotto(tipo='prodotto', descrizione='')
    prodotto.titolo = articolo.nome
    prodotto.gruppo = articolo.categoria
    prodotto.codice_prodotto = articolo.codice
    prodotto.attivo = True
    if prezzo is not None:
        prodotto.prezzo = prezzo
    if categoria is not None:
        prodotto.categoria = categoria
    prodotto._da_magazzino = True   # il signal non deve creare un altro articolo
    prodotto.save()
    if articolo.prodotto_id != prodotto.pk:
        articolo.prodotto = prodotto
        articolo.save(update_fields=['prodotto'])
    sincronizza_prodotto(articolo)
    return prodotto


def scarica_vendita(prodotto, delta, ordine=None, operatore=None, nota=''):
    """Vendita (delta < 0) o reso/correzione (delta > 0) di un prodotto
    del catalogo in un ordine cliente."""
    articolo = articolo_del_prodotto(prodotto)
    if articolo is None or not delta:
        return None
    return movimenta(articolo, delta, 'vendita', operatore=operatore, ordine=ordine, nota=nota)


# ---------------------------------------------------------------------------
# Foto
# ---------------------------------------------------------------------------

def salva_foto(articolo, file):
    """Ridimensiona (lato massimo 1000 px, orientamento della fotocamera)
    e salva come JPEG nel database. ValueError se non e' un'immagine."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if file.size > FOTO_PESO_MAX:
        raise ValueError('Immagine troppo grande (massimo 20 MB)')
    try:
        img = Image.open(file)
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError):
        raise ValueError("Il file non è un'immagine valida")
    if img.mode in ('RGBA', 'LA', 'P'):
        sfondo = Image.new('RGB', img.size, 'white')
        img = img.convert('RGBA')
        sfondo.paste(img, mask=img.split()[-1])
        img = sfondo
    else:
        img = img.convert('RGB')
    img.thumbnail((FOTO_LATO_MAX, FOTO_LATO_MAX))
    buffer = BytesIO()
    img.save(buffer, 'JPEG', quality=82, optimize=True)
    with transaction.atomic():
        FotoArticolo.objects.update_or_create(articolo=articolo,
                                              defaults={'dati': buffer.getvalue(), 'tipo': 'image/jpeg'})
        articolo.foto_aggiornata = timezone.now()
        articolo.save(update_fields=['foto_aggiornata'])


def elimina_foto(articolo):
    FotoArticolo.objects.filter(articolo=articolo).delete()
    articolo.foto_aggiornata = None
    articolo.save(update_fields=['foto_aggiornata'])


# ---------------------------------------------------------------------------
# Assegnazioni
# ---------------------------------------------------------------------------

@transaction.atomic
def assegna(articolo, quantita, utente=None, postazione=None, da=None, note='',
            in_apertura=True, in_chiusura=True, origine=None):
    if quantita <= 0:
        raise ValueError('Quantità non valida')
    if bool(utente) == bool(postazione):
        raise ValueError('Scegli un operatore oppure una postazione')
    a = Assegnazione.objects.create(
        articolo=articolo, quantita=quantita, utente=utente, postazione=postazione,
        assegnata_da=da, note=note[:255], in_apertura=in_apertura, in_chiusura=in_chiusura,
        origine=origine)
    StatoAssegnazione.objects.create(assegnazione=a, a_stato='in_uso', quantita=quantita,
                                     origine='magazzino', utente=da, nota=note[:255])
    movimenta(articolo, -quantita, 'assegnazione', operatore=da, assegnazione=a,
              nota=f'A {a.destinatario}')
    sincronizza_checklist(a)
    return a


@transaction.atomic
def cambia_stato(assegnazione, stato, quantita=None, utente=None, origine='magazzino', nota=''):
    """Cambia lo stato di (una parte di) un'assegnazione. Se la quantita'
    e' minore del totale l'assegnazione si divide: la parte segnalata
    diventa una nuova assegnazione. Restituisce l'assegnazione cambiata,
    o None se non c'era niente da fare."""
    a = Assegnazione.objects.select_for_update().get(pk=assegnazione.pk)
    if stato not in dict(Assegnazione.STATO_CHOICES):
        raise ValueError('Stato non valido')
    if not a.aperta or stato == a.stato:
        return None
    quantita = a.quantita if quantita is None else int(quantita)
    if quantita <= 0 or quantita > a.quantita:
        raise ValueError(f'Quantità tra 1 e {a.quantita}')

    if quantita < a.quantita:
        a.quantita -= quantita
        a.save(update_fields=['quantita'])
        sincronizza_checklist(a)
        a = Assegnazione.objects.create(
            articolo=a.articolo, quantita=quantita, utente=a.utente, postazione=a.postazione,
            stato=a.stato, in_apertura=a.in_apertura, in_chiusura=a.in_chiusura,
            assegnata_da=a.assegnata_da, data=a.data, note=a.note, origine=a)

    StatoAssegnazione.objects.create(assegnazione=a, da_stato=a.stato, a_stato=stato,
                                     quantita=quantita, origine=origine, utente=utente,
                                     nota=nota[:255])
    a.stato = stato
    a.chiusa_il = None if stato in Assegnazione.APERTI else timezone.now()
    a.da_gestire = origine != 'magazzino' and stato in Assegnazione.SEGNALABILI
    a.save(update_fields=['stato', 'chiusa_il', 'da_gestire'])
    if stato == 'restituito':
        movimenta(a.articolo, quantita, 'rientro', operatore=utente, assegnazione=a,
                  nota=f'Da {a.destinatario}')
    sincronizza_checklist(a)
    return a


@transaction.atomic
def sostituisci(assegnazione, da=None, nota=''):
    """Consegna un pezzo nuovo al posto di quello segnalato: il vecchio,
    se ancora presso il destinatario, passa a 'sostituito'."""
    a = assegnazione
    if a.aperta:
        cambia_stato(a, 'sostituito', utente=da, nota=nota)
        a.refresh_from_db()
    if a.da_gestire:
        a.da_gestire = False
        a.save(update_fields=['da_gestire'])
    return assegna(a.articolo, a.quantita, utente=a.utente, postazione=a.postazione, da=da,
                   note=nota or 'Sostituzione', in_apertura=a.in_apertura,
                   in_chiusura=a.in_chiusura, origine=a)


def puo_segnalare(user, assegnazione):
    """L'operatore cambia lo stato delle sue assegnazioni e di quelle
    delle postazioni del suo turno attivo; il magazzino di tutte."""
    from apps.auth_system.sezioni import ha_accesso

    if ha_accesso(user, 'magazzino'):
        return True
    if assegnazione.utente_id == user.pk:
        return True
    return assegnazione.postazione_id in postazioni_del_turno(user)


def postazioni_del_turno(user):
    from apps.turni.models import PostazioneTurno

    return set(PostazioneTurno.objects.filter(sessione__operatore=user, sessione__stato='attivo')
               .values_list('postazione_cq_id', flat=True))


# ---------------------------------------------------------------------------
# Checklist di turno
# ---------------------------------------------------------------------------

def categoria_dotazione():
    from apps.turni.models import CategoriaChecklist, EsitoChecklist

    cat, creata = CategoriaChecklist.objects.get_or_create(
        nome=CATEGORIA_DOTAZIONE, defaults={'icona': 'bi-box-seam', 'ordine': 0})
    if creata or not cat.esiti.exists():
        for codice, nome, colore, ordine in ESITI_DOTAZIONE:
            EsitoChecklist.objects.get_or_create(
                categoria=cat, codice=codice,
                defaults={'nome': nome, 'colore': colore, 'ordine': ordine})
    return cat


def sincronizza_checklist(assegnazione):
    """La merce assegnata a una postazione e' una voce della checklist di
    inizio/fine turno di quella postazione, finche' l'assegnazione e'
    aperta."""
    from apps.turni.models import ChecklistItem

    a = assegnazione
    voce = ChecklistItem.objects.filter(assegnazione=a).first()
    if not a.postazione_id or not a.aperta:
        if voce and voce.attivo:
            voce.attivo = False
            voce.save(update_fields=['attivo'])
        return voce
    valori = {
        'postazione_cq_id': a.postazione_id,
        'categoria': categoria_dotazione(),
        'nome': f'{a.articolo.nome} ×{a.quantita}',
        'in_apertura': a.in_apertura,
        'in_chiusura': a.in_chiusura,
        'attivo': True,
    }
    if voce is None:
        return ChecklistItem.objects.create(assegnazione=a, **valori)
    for campo, valore in valori.items():
        setattr(voce, campo, valore)
    voce.save()
    return voce


def esito_checklist(voce, esito_obj, utente, nota=''):
    """Compilazione della checklist: un esito non OK su una voce del
    magazzino cambia lo stato dell'assegnazione."""
    if not voce.assegnazione_id or esito_obj is None:
        return None
    stato = ESITO_A_STATO.get(esito_obj.codice)
    if stato is None:
        return None
    return cambia_stato(voce.assegnazione, stato, utente=utente, origine='checklist',
                        nota=nota or 'Dalla checklist di turno')
