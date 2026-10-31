"""Lettura degli estratti conto scaricati dall'home banking e import
idempotente nella prima nota.

Formati riconosciuti da soli:
- Excel .xlsx o CSV con una riga di intestazione: le colonne si trovano
  per nome (data/data contabile/data movimento, valuta, importo oppure
  dare/avere o addebiti/accrediti, descrizione, causale, saldo). E' il
  caso di MPS ("DATA CONT. | DATA VAL. | CAUSALE | DESCRIZIONE | IMPORTO")
  e di BAPS ("Data movimento | Data valuta | Descrizione operazione |
  Causale | Importo");
- BAPS senza intestazione: data, valuta, EUR, importo, descrizione, saldo;
- "xls" che in realta' sono pagine HTML con una tabella (molte banche).

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

from .models import MovimentoFinanziario, RegolaMovimento


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
    if valore is None or valore == '':
        return None
    if isinstance(valore, (int, float, Decimal)):
        return Decimal(str(valore)).quantize(Decimal('0.01'))
    testo = re.sub(r'[^\d,.\-+]', '', str(valore))
    if not testo or testo in '-+':
        return None
    if ',' in testo:                                  # formato italiano
        testo = testo.replace('.', '').replace(',', '.')
    elif testo.count('.') > 1:                        # 1.234.567 senza decimali
        testo = testo.replace('.', '')
    try:
        return Decimal(testo).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


def _spazi(testo):
    return re.sub(r'\s+', ' ', str(testo or '')).strip()


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
            self._riga.append(_spazi(''.join(self._cella)))
            self._cella = None
        elif tag == 'tr' and self._riga is not None:
            self.righe.append(self._riga)
            self._riga = None

    def handle_data(self, data):
        if self._cella is not None:
            self._cella.append(data)


def righe_file(nome, dati):
    """Tutte le righe del file come liste di valori."""
    if dati[:4] == b'\xd0\xcf\x11\xe0':
        raise ErroreEstratto('file Excel vecchio formato (.xls): aprilo e salvalo come .xlsx, oppure scarica il CSV')
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
    if '<t' in testo[:5000].lower() and '<td' in testo.lower():
        tabella = _TabellaHTML()
        tabella.feed(testo)
        return tabella.righe
    try:
        separatore = csv.Sniffer().sniff(testo[:5000], delimiters=';,\t').delimiter
    except csv.Error:
        separatore = ';'
    return list(csv.reader(io.StringIO(testo), delimiter=separatore))


# ---------------------------------------------------------------- righe -> movimenti

COLONNE = {
    'data': ('data cont', 'data operazione', 'data movimento', 'data contabile', 'data reg', 'data'),
    'valuta': ('valuta',),
    'importo': ('importo',),
    'dare': ('dare', 'addebiti', 'uscite', 'addebito'),
    'avere': ('avere', 'accrediti', 'entrate', 'accredito'),
    'descrizione': ('descrizione', 'dettaglio', 'operazione'),
    'causale': ('causale',),
    'saldo': ('saldo',),
}


def _intestazione(riga):
    """{campo: indice} se la riga e' un'intestazione."""
    testi = [_spazi(c).lower() for c in riga]
    trovate = {}
    for campo, nomi in COLONNE.items():
        for i, t in enumerate(testi):
            if i in trovate.values() or not t:
                continue
            if any(t.startswith(n) or n in t for n in nomi):
                if campo == 'data' and 'valuta' in t:
                    continue
                trovate[campo] = i
                break
    if 'data' in trovate and ('importo' in trovate or 'dare' in trovate or 'avere' in trovate):
        return trovate
    return None


def _valore(riga, colonne, campo):
    i = colonne.get(campo)
    return riga[i] if i is not None and i < len(riga) else None


def movimenti_da_righe(righe):
    """[{data, data_valuta, importo, descrizione, causale, saldo}]"""
    colonne = None
    movimenti = []
    for riga in righe:
        if not riga or all(c in (None, '') for c in riga):
            continue
        if colonne is None:
            colonne = _intestazione(riga)
            if colonne is not None:
                continue
            # BAPS senza intestazione: data, valuta, EUR, importo, descrizione, saldo
            if len(riga) >= 6 and leggi_data(riga[0]) and str(riga[2] or '').strip().upper() == 'EUR':
                movimenti.append({
                    'data': leggi_data(riga[0]), 'data_valuta': leggi_data(riga[1]),
                    'importo': leggi_importo(riga[3]), 'descrizione': _spazi(riga[4]), 'causale': '',
                    'saldo': leggi_importo(riga[5])})
            continue
        data = leggi_data(_valore(riga, colonne, 'data'))
        if not data:
            continue                                  # totali, saldi, righe vuote
        importo = leggi_importo(_valore(riga, colonne, 'importo'))
        if importo is None:
            dare = leggi_importo(_valore(riga, colonne, 'dare')) or Decimal('0')
            avere = leggi_importo(_valore(riga, colonne, 'avere')) or Decimal('0')
            importo = avere - abs(dare)
        if not importo:
            continue
        movimenti.append({
            'data': data, 'data_valuta': leggi_data(_valore(riga, colonne, 'valuta')),
            'importo': importo,
            'descrizione': _spazi(_valore(riga, colonne, 'descrizione')).strip('"'),
            'causale': _spazi(_valore(riga, colonne, 'causale')).strip('"')[:120],
            'saldo': leggi_importo(_valore(riga, colonne, 'saldo'))})
    movimenti = [m for m in movimenti if m['importo']]
    if not movimenti:
        raise ErroreEstratto('nessun movimento riconosciuto: servono almeno le colonne data e importo')
    return movimenti


def impronte(movimenti):
    contatore = {}
    for m in movimenti:
        chiave = f"{m['data']:%Y-%m-%d}|{m['importo']}|{m['descrizione'].lower()}|{m['causale'].lower()}"
        n = contatore[chiave] = contatore.get(chiave, 0) + 1
        m['impronta'] = hashlib.sha1(f'{chiave}|{n}'.encode()).hexdigest()
    return movimenti


@dataclass
class EsitoEstratto:
    nuovi: int = 0
    doppi: int = 0
    classificati: int = 0
    dal: date = None
    al: date = None
    errori: list = field(default_factory=list)


def importa_estratto(conto, nome, dati):
    esito = EsitoEstratto()
    try:
        movimenti = impronte(movimenti_da_righe(righe_file(nome, dati)))
    except ErroreEstratto as e:
        esito.errori.append(str(e))
        return esito
    esito.dal, esito.al = min(m['data'] for m in movimenti), max(m['data'] for m in movimenti)
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
