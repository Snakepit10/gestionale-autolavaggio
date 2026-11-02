"""Lettura degli estratti conto scaricati dall'home banking e import
idempotente nella prima nota.

Il file diventa una tabella di righe (xlsx, xls vecchio formato, CSV o
"xls" che sono pagine HTML); poi un *modello* dice in quali colonne
stanno data, valuta, importo (o dare/avere), descrizione, causale e
saldo. I modelli sono:
- con intestazione: le colonne si trovano per nome;
- predefiniti senza intestazione (MODELLI_PREDEFINITI): riconosciuti da
  come sono fatte le righe;
- personalizzati (ModelloEstratto), creati dalla pagina Modelli per le
  banche che i predefiniti non conoscono.
Il conto puo' avere un modello fisso; altrimenti si sceglie da solo
quello che legge piu' righe. Le righe senza una data valida
(intestazioni, totali) si saltano; una riga "saldo" senza data
(es. in fondo all'export MPS) diventa il saldo finale dell'estratto.

La stessa riga non entra due volte: l'impronta e' data, importo,
descrizione e il numero d'ordine fra le righe identiche dello stesso
giorno (due POS da 8 euro nello stesso giorno restano due)."""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from django.db import transaction

from .models import ModelloEstratto, MovimentoFinanziario, RegolaMovimento


class ErroreEstratto(Exception):
    pass


# ---------------------------------------------------------------- valori

def leggi_data(valore):
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    testo = str(valore or '').strip()
    for formato in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(testo[:10], formato).date()
        except ValueError:
            continue
    return None


def leggi_importo(valore):
    """'-1.234,56', '1.234,56', '25', '€ -9,99', 17.5 -> Decimal; '' -> None."""
    if valore is None or valore == '' or isinstance(valore, bool):
        return None
    if isinstance(valore, (int, float, Decimal)):
        return Decimal(str(valore)).quantize(Decimal('0.01'))
    testo = re.sub(r'[^\d,.\-+]', '', str(valore))
    if not testo or not re.search(r'\d', testo):
        return None
    if ',' in testo:                                  # formato italiano
        testo = testo.replace('.', '').replace(',', '.')
    elif testo.count('.') > 1:                        # 1.234.567 senza decimali
        testo = testo.replace('.', '')
    try:
        return Decimal(testo).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


def _testo(valore):
    """Spazi e a capo compattati, virgolette di contorno tolte."""
    return re.sub(r'\s+', ' ', str(valore if valore is not None else '')).strip().strip('"').strip()


def colonna(testo):
    """'C' o '3' -> 3 (colonne come in Excel, A = 1); vuoto -> None."""
    testo = (testo or '').strip().upper()
    if not testo:
        return None
    if testo.isdigit():
        return int(testo) or None
    if re.fullmatch(r'[A-Z]{1,2}', testo):
        n = 0
        for lettera in testo:
            n = n * 26 + ord(lettera) - 64
        return n
    raise ValueError(f'colonna "{testo}" non valida: usa la lettera (A, B, ...) o il numero')


def lettera(n):
    if not n:
        return ''
    testo = ''
    while n:
        n, resto = divmod(n - 1, 26)
        testo = chr(65 + resto) + testo
    return testo


# ---------------------------------------------------------------- file -> righe

class _TabellaHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.righe, self._riga, self._cella = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._riga = []
        elif tag in ('td', 'th') and self._riga is not None:
            self._cella = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cella is not None:
            self._riga.append(_testo(''.join(self._cella)))
            self._cella = None
        elif tag == 'tr' and self._riga is not None:
            self.righe.append(self._riga)
            self._riga = None

    def handle_data(self, data):
        if self._cella is not None:
            self._cella.append(data)


def _righe_xls(dati):
    """Excel 97-2003 (.xls, formato OLE): lo legge xlrd."""
    try:
        import xlrd
    except ImportError:
        raise ErroreEstratto('file Excel vecchio formato (.xls): salvalo come .xlsx, oppure scarica il CSV')
    try:
        libro = xlrd.open_workbook(file_contents=dati)
    except xlrd.XLRDError as e:
        raise ErroreEstratto(f'il file .xls non si apre ({e})')
    righe = []
    for foglio in libro.sheets():
        for i in range(foglio.nrows):
            riga = []
            for cella in foglio.row(i):
                if cella.ctype == xlrd.XL_CELL_DATE:
                    riga.append(xlrd.xldate_as_datetime(cella.value, libro.datemode))
                elif cella.ctype == xlrd.XL_CELL_EMPTY:
                    riga.append(None)
                else:
                    riga.append(cella.value)
            righe.append(riga)
    return righe


def righe_file(nome, dati):
    """Tutte le righe del file come liste di valori."""
    if dati[:4] == b'\xd0\xcf\x11\xe0':
        return _righe_xls(dati)
    if dati[:2] == b'PK':
        import openpyxl
        try:
            libro = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
        except Exception as e:
            raise ErroreEstratto(f'il file Excel non si apre ({e})')
        righe = []
        for foglio in libro.worksheets:
            righe.extend(list(r) for r in foglio.iter_rows(values_only=True))
        return righe
    for codifica in ('utf-8-sig', 'cp1252'):
        try:
            testo = dati.decode(codifica)
            break
        except UnicodeDecodeError:
            continue
    if '<td' in testo.lower():
        tabella = _TabellaHTML()
        tabella.feed(testo)
        return tabella.righe
    try:
        separatore = csv.Sniffer().sniff(testo[:5000], delimiters=';,\t').delimiter
    except csv.Error:
        separatore = ';'
    return list(csv.reader(io.StringIO(testo), delimiter=separatore))


# ---------------------------------------------------------------- modelli

@dataclass
class Modello:
    """Colonne (da 0) di un estratto. `firma(riga)` dice se una riga con
    la data ha davvero la forma del modello: serve a riconoscerlo."""
    chiave: str
    nome: str
    data: int
    importo: int = None
    dare: int = None
    avere: int = None
    valuta: int = None
    descrizione: int = None
    causale: int = None
    saldo: int = None
    firma: object = None
    spiegazione: str = ''

    def colonne(self):
        """[(campo, lettera)] per mostrarlo nelle pagine."""
        campi = [('Data', self.data), ('Valuta', self.valuta), ('Importo', self.importo), ('Dare (uscite)', self.dare),
                 ('Avere (entrate)', self.avere), ('Causale', self.causale), ('Descrizione', self.descrizione),
                 ('Saldo', self.saldo)]
        return [(nome, lettera(i + 1)) for nome, i in sorted((c for c in campi if c[1] is not None), key=lambda c: c[1])]


def _cella(riga, i):
    return riga[i] if i is not None and i < len(riga) else None


def _vuota(valore):
    return valore is None or str(valore).strip().strip('"').strip() in ('', '-')


def _e_numero(valore):
    return leggi_importo(valore) is not None


MODELLI_PREDEFINITI = [
    Modello('mps', 'MPS - Excel senza intestazione', data=0, valuta=1, causale=2, descrizione=3, importo=5,
            firma=lambda r: (leggi_data(_cella(r, 1)) is not None and not _e_numero(_cella(r, 2))
                             and _vuota(_cella(r, 4)) and _e_numero(_cella(r, 5))),
            spiegazione='Data, valuta, causale, descrizione, colonna vuota, importo con segno. In fondo una riga '
                        '"Totale" senza data: serve a controllare che la somma dei movimenti letti torni.'),
    Modello('baps_xlsx', 'BAPS - Excel senza intestazione, con saldo', data=0, valuta=1, importo=3, descrizione=4,
            saldo=5,
            firma=lambda r: (str(_cella(r, 2) or '').strip().upper() == 'EUR' and _e_numero(_cella(r, 3))),
            spiegazione='Data, valuta, divisa (EUR), importo con segno, descrizione, saldo dopo il movimento.'),
    Modello('baps_xls', 'BAPS - Excel 97-2003 (.xls) senza intestazione', data=0, valuta=1, importo=3, descrizione=4,
            firma=lambda r: (leggi_data(_cella(r, 1)) is not None and _vuota(_cella(r, 2))
                             and _e_numero(_cella(r, 3)) and not _e_numero(_cella(r, 5))),
            spiegazione='Data, valuta, colonna vuota, importo con segno, descrizione, nota personale (ignorata).'),
]
PREDEFINITI = {m.chiave: m for m in MODELLI_PREDEFINITI}

COLONNE_INTESTAZIONE = {
    'data': ('data cont', 'data operazione', 'data movimento', 'data contabile', 'data reg', 'data'),
    'valuta': ('valuta',),
    'importo': ('importo',),
    'dare': ('dare', 'addebiti', 'uscite', 'addebito'),
    'avere': ('avere', 'accrediti', 'entrate', 'accredito'),
    'descrizione': ('descrizione', 'dettaglio', 'operazione'),
    'causale': ('causale',),
    'saldo': ('saldo',),
}


def modello_personalizzato(m):
    meno_uno = lambda n: n - 1 if n else None    # noqa: E731  colonne da 1 -> da 0
    return Modello(f'pers_{m.pk}', m.nome, data=m.col_data - 1, importo=meno_uno(m.col_importo),
                   dare=meno_uno(m.col_dare), avere=meno_uno(m.col_avere), valuta=meno_uno(m.col_valuta),
                   descrizione=meno_uno(m.col_descrizione), causale=meno_uno(m.col_causale),
                   saldo=meno_uno(m.col_saldo), spiegazione=m.note)


def modelli_disponibili():
    """Predefiniti + personalizzati, per le scelte nelle pagine."""
    return MODELLI_PREDEFINITI + [modello_personalizzato(m) for m in ModelloEstratto.objects.all()]


def modello_da_chiave(chiave):
    if chiave in PREDEFINITI:
        return PREDEFINITI[chiave]
    if chiave.startswith('pers_') and chiave[5:].isdigit():
        m = ModelloEstratto.objects.filter(pk=chiave[5:]).first()
        return modello_personalizzato(m) if m else None
    return None


def _intestazione(riga):
    """Modello con le colonne trovate per nome, se la riga e' un'intestazione."""
    if any(leggi_data(c) or _e_numero(c) for c in riga if not _vuota(c)):
        return None                                   # una riga di dati, non di titoli
    testi = [_testo(c).lower() for c in riga]
    trovate = {}
    for campo, nomi in COLONNE_INTESTAZIONE.items():
        for i, t in enumerate(testi):
            if i in trovate.values() or not t or re.search(r'\d', t):
                continue
            if any(t.startswith(n) or n in t for n in nomi):
                if campo == 'data' and 'valuta' in t:
                    continue
                trovate[campo] = i
                break
    if 'data' in trovate and ({'importo', 'dare', 'avere'} & trovate.keys()):
        return Modello('intestazione', 'Con intestazione (colonne riconosciute per nome)', **trovate)
    return None


# ---------------------------------------------------------------- righe -> movimenti

@dataclass
class Lettura:
    modello: Modello
    movimenti: list
    saldo_finale: Decimal = None
    scartate: int = 0                                 # righe con la data ma senza importo
    firme: int = 0                                    # righe con la forma del modello
    totale_file: Decimal = None                       # riga "Totale" del file, per il controllo

    @property
    def quadra_col_totale(self):
        if self.totale_file is None:
            return None
        return sum((m['importo'] for m in self.movimenti), Decimal('0')) == self.totale_file

    @property
    def saldi_coerenti(self):
        """Nell'ordine del file, ogni saldo = saldo precedente + importo."""
        con_saldo = [m for m in self.movimenti if m['saldo'] is not None]
        if len(con_saldo) < 2:
            return None
        return all(b['saldo'] == a['saldo'] + b['importo'] for a, b in zip(con_saldo, con_saldo[1:]))


def leggi_con_modello(righe, modello, salta_fino=0):
    movimenti, saldo_finale, totale_file, scartate, firme = [], None, None, 0, 0
    for riga in righe[salta_fino:]:
        if not riga or all(_vuota(c) for c in riga):
            continue
        data = leggi_data(_cella(riga, modello.data))
        if not data:
            # righe senza data: "Saldo" finale o "Totale" dei movimenti (es. in
            # fondo all'export MPS), usate per i controlli
            testo = ' '.join(_testo(c) for c in riga if isinstance(c, str)).lower()
            numero = leggi_importo(_cella(riga, modello.importo if modello.importo is not None else modello.saldo))
            if numero is not None and 'saldo' in testo:
                saldo_finale = numero
            elif numero is not None and 'totale' in testo:
                totale_file = numero
            continue
        importo = leggi_importo(_cella(riga, modello.importo))
        if importo is None and (modello.dare is not None or modello.avere is not None):
            dare = leggi_importo(_cella(riga, modello.dare)) or Decimal('0')
            avere = leggi_importo(_cella(riga, modello.avere)) or Decimal('0')
            importo = avere - abs(dare)
        if not importo:
            scartate += 1
            continue
        if modello.firma and modello.firma(riga):
            firme += 1
        movimenti.append({
            'data': data, 'data_valuta': leggi_data(_cella(riga, modello.valuta)), 'importo': importo,
            'descrizione': _testo(_cella(riga, modello.descrizione)),
            'causale': _testo(_cella(riga, modello.causale))[:120],
            'saldo': leggi_importo(_cella(riga, modello.saldo)) if modello.saldo is not None else None})
    return Lettura(modello, movimenti, saldo_finale, scartate, firme, totale_file)


def leggi_righe(righe, modello=None):
    """Movimenti col modello dato, o col modello che si riconosce."""
    if modello is not None:
        lettura = leggi_con_modello(righe, modello)
    else:
        lettura = _riconosci(righe)
    if not lettura or not lettura.movimenti:
        raise ErroreEstratto('nessun movimento riconosciuto: scegli il modello giusto o creane uno '
                             'personalizzato dalla pagina Modelli di estratto')
    return lettura


def _riconosci(righe):
    for i, riga in enumerate(righe[:30]):            # intestazione nelle prime righe
        if riga:
            modello = _intestazione(riga)
            if modello:
                return leggi_con_modello(righe, modello, salta_fino=i + 1)
    candidati = []
    for modello in MODELLI_PREDEFINITI:
        lettura = leggi_con_modello(righe, modello)
        # il modello vale se quasi tutte le righe lette ne hanno la forma
        if lettura.movimenti and lettura.firme >= 0.8 * len(lettura.movimenti):
            candidati.append(lettura)
    return max(candidati, key=lambda l: (len(l.movimenti), l.firme)) if candidati else None


def movimenti_da_righe(righe, modello=None):
    return leggi_righe(righe, modello).movimenti


def impronte(movimenti):
    contatore = {}
    for m in movimenti:
        chiave = f"{m['data']:%Y-%m-%d}|{m['importo']}|{m['descrizione'].lower()}|{m['causale'].lower()}"
        n = contatore[chiave] = contatore.get(chiave, 0) + 1
        m['impronta'] = hashlib.sha1(f'{chiave}|{n}'.encode()).hexdigest()
    return movimenti


def anteprima(nome, dati, modello=None, quante=15):
    """Come verrebbe letto il file, senza importare niente."""
    lettura = leggi_righe(righe_file(nome, dati), modello)
    movimenti = sorted(lettura.movimenti, key=lambda m: m['data'])
    return {
        'modello': lettura.modello,
        'n': len(movimenti),
        'dal': movimenti[0]['data'], 'al': movimenti[-1]['data'],
        'entrate': sum((m['importo'] for m in movimenti if m['importo'] > 0), Decimal('0')),
        'uscite': sum((m['importo'] for m in movimenti if m['importo'] < 0), Decimal('0')),
        'saldo_finale': lettura.saldo_finale,
        'totale_file': lettura.totale_file,
        'quadra_col_totale': lettura.quadra_col_totale,
        'saldi_coerenti': lettura.saldi_coerenti,
        'scartate': lettura.scartate,
        'esempi': movimenti[:quante],
    }


@dataclass
class EsitoEstratto:
    nuovi: int = 0
    doppi: int = 0
    classificati: int = 0
    dal: date = None
    al: date = None
    modello: str = ''
    quadra_col_totale: bool = None
    errori: list = field(default_factory=list)


def importa_estratto(conto, nome, dati, modello=None):
    """Importa l'estratto nel conto. Modello: quello passato, poi quello
    del conto, altrimenti riconosciuto dal file."""
    esito = EsitoEstratto()
    if modello is None and conto.modello_estratto:
        modello = modello_da_chiave(conto.modello_estratto)
    try:
        lettura = leggi_righe(righe_file(nome, dati), modello)
    except ErroreEstratto as e:
        esito.errori.append(str(e))
        return esito
    movimenti = impronte(lettura.movimenti)
    esito.modello = lettura.modello.nome
    esito.quadra_col_totale = lettura.quadra_col_totale
    esito.dal, esito.al = min(m['data'] for m in movimenti), max(m['data'] for m in movimenti)
    if lettura.saldo_finale is not None:
        # il saldo finale va sull'ultimo giorno: serve al controllo del saldo nei Conti
        next(m for m in movimenti if m['data'] == esito.al)['saldo'] = lettura.saldo_finale
    esistenti = set(conto.movimenti.filter(impronta__in=[m['impronta'] for m in movimenti])
                    .values_list('impronta', flat=True))
    regole = list(RegolaMovimento.objects.select_related('voce'))
    nuovi = []
    for m in movimenti:
        if m['impronta'] in esistenti:
            esito.doppi += 1
            continue
        mov = MovimentoFinanziario(
            conto=conto, data=m['data'], data_valuta=m['data_valuta'], importo=m['importo'],
            descrizione=m['descrizione'], causale=m['causale'], saldo_estratto=m['saldo'],
            impronta=m['impronta'], origine='estratto')
        if mov.classifica(regole):
            esito.classificati += 1
        nuovi.append(mov)
        esistenti.add(m['impronta'])
    with transaction.atomic():
        MovimentoFinanziario.objects.bulk_create(nuovi)
    esito.nuovi = len(nuovi)
    return esito
