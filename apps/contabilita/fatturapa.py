"""Lettura delle fatture elettroniche ricevute dallo SDI (FatturaPA 1.2.x):
XML, XML firmato (.p7m, anche codificato in base64) e ZIP che li
contengono (es. lo scarico del cassetto fiscale). Solo libreria standard.

`estrai_xml(nome, dati)` -> [(nome, xml)]
`leggi_fattura(xml)` -> un dict per documento (di solito uno per file)."""
import base64
import binascii
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation

CENTESIMO = Decimal('0.01')
LIMITE_FILE = 20 * 1024 * 1024
ESTENSIONI = ('.xml', '.p7m', '.zip')
# File dello SDI che non sono fatture: metadati, ricevute e notifiche
_NON_FATTURE = re.compile(r'_(MT|RC|NS|MC|NE|DT|AT|EC|SE)_[0-9A-Za-z]+\.', re.I)
TIPI_NOTA_CREDITO = ('TD04', 'TD08')


class ErroreFattura(Exception):
    pass


def e_fattura(nome):
    """Il nome del file puo' essere una fattura (o uno ZIP di fatture)?"""
    base = nome.rsplit('/', 1)[-1]
    if not base.lower().endswith(ESTENSIONI) or base.lower() == 'daticert.xml':
        return False
    return not _NON_FATTURE.search(base)


# ---------------------------------------------------------------- contenitori

def estrai_xml(nome, dati):
    basso = nome.lower()
    if basso.endswith('.zip'):
        try:
            archivio = zipfile.ZipFile(io.BytesIO(dati))
        except zipfile.BadZipFile:
            raise ErroreFattura(f'{nome}: lo ZIP non si apre')
        trovati = []
        with archivio:
            for info in archivio.infolist():
                interno = info.filename.rsplit('/', 1)[-1]
                if info.is_dir() or not e_fattura(interno):
                    continue
                if info.file_size > LIMITE_FILE:
                    raise ErroreFattura(f'{interno}: file troppo grande')
                trovati.extend(estrai_xml(interno, archivio.read(info)))
        return trovati
    if basso.endswith('.p7m'):
        return [(nome[:-4], xml_da_p7m(dati))]
    return [(nome, dati)]


def xml_da_p7m(dati):
    """XML contenuto in una busta CAdES (.p7m). La busta e' DER/BER, a
    volte in base64; il contenuto puo' essere spezzato in piu' OCTET
    STRING (BER a lunghezza indefinita): si ricompone."""
    dati = dati.strip()
    if not dati.startswith(b'\x30'):
        try:
            dati = base64.b64decode(dati)
        except (binascii.Error, ValueError):
            raise ErroreFattura('il file firmato non e\' leggibile')
    try:
        elementi, _ = _asn1(dati, 0, len(dati), 0)
        for contenuto in _ottetti(elementi):
            if b'FatturaElettronica' in contenuto:
                return contenuto[contenuto.find(b'<'):]
    except (IndexError, ValueError):
        pass
    # Ripiego: XML in chiaro dentro la busta
    inizio, fine = dati.find(b'<?xml'), dati.rfind(b'FatturaElettronica>')
    if inizio >= 0 and fine > inizio:
        return dati[inizio:fine + len(b'FatturaElettronica>')]
    raise ErroreFattura('nel file firmato non c\'e\' una fattura')


def _asn1(dati, pos, fine, profondita):
    """Elementi ASN.1 tra pos e fine (fine None = lunghezza indefinita,
    chiusa da 00 00): lista di (tag, bytes | lista di figli), posizione."""
    elementi = []
    limite = len(dati) if fine is None else fine
    while pos < limite:
        if fine is None and dati[pos:pos + 2] == b'\x00\x00':
            return elementi, pos + 2
        tag = dati[pos]
        pos += 1
        if tag & 0x1F == 0x1F:                        # tag su piu' byte
            while dati[pos] & 0x80:
                pos += 1
            pos += 1
        lunghezza = dati[pos]
        pos += 1
        if lunghezza == 0x80:
            fine_el = None
        else:
            if lunghezza & 0x80:
                n = lunghezza & 0x7F
                lunghezza = int.from_bytes(dati[pos:pos + n], 'big')
                pos += n
            fine_el = pos + lunghezza
            if fine_el > len(dati):
                raise ValueError('lunghezza oltre la fine')
        if tag & 0x20:                                # costruito
            if profondita > 40:
                raise ValueError('troppo annidato')
            try:
                figli, dopo = _asn1(dati, pos, fine_el, profondita + 1)
            except (IndexError, ValueError):
                if fine_el is None:
                    raise
                figli = []                            # parte illeggibile (es. firma): si salta
            pos = dopo if fine_el is None else fine_el
            elementi.append((tag, figli))
        else:
            if fine_el is None:
                raise ValueError('primitivo a lunghezza indefinita')
            elementi.append((tag, dati[pos:fine_el]))
            pos = fine_el
    if fine is None:
        raise ValueError('manca la chiusura')
    return elementi, pos


def _ottetti(elementi):
    for tag, valore in elementi:
        if tag == 0x04:
            yield valore
        elif tag == 0x24:                             # OCTET STRING spezzata
            yield b''.join(_pezzi(valore))
        elif isinstance(valore, list):
            yield from _ottetti(valore)


def _pezzi(elementi):
    for tag, valore in elementi:
        if tag == 0x04:
            yield valore
        elif tag == 0x24:
            yield from _pezzi(valore)


# ---------------------------------------------------------------- XML

def _testo(el, percorso):
    trovato = el.find(percorso) if el is not None else None
    return (trovato.text or '').strip() if trovato is not None and trovato.text else ''


def _decimale(testo, default=Decimal('0')):
    if not testo:
        return default
    try:
        return Decimal(testo)
    except InvalidOperation:
        raise ErroreFattura(f'importo non valido: "{testo}"')


def _data(testo):
    try:
        return date.fromisoformat(testo[:10])
    except ValueError:
        raise ErroreFattura(f'data non valida: "{testo}"')


def leggi_fattura(xml):
    xml = xml.lstrip(b'\xef\xbb\xbf \r\n\t')
    fine = xml.rfind(b'FatturaElettronica>')
    if fine > 0:                                      # scarti dopo la chiusura
        xml = xml[:fine + len(b'FatturaElettronica>')]
    try:
        radice = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ErroreFattura(f'XML non valido ({e})')
    for el in radice.iter():                          # niente namespace
        if isinstance(el.tag, str):
            el.tag = el.tag.rsplit('}', 1)[-1]
    if radice.tag != 'FatturaElettronica':
        raise ErroreFattura('non e\' una fattura elettronica')

    cedente = radice.find('FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici')
    if cedente is None:
        raise ErroreFattura('manca il fornitore (CedentePrestatore)')
    denominazione = _testo(cedente, 'Anagrafica/Denominazione') or ' '.join(
        x for x in (_testo(cedente, 'Anagrafica/Nome'), _testo(cedente, 'Anagrafica/Cognome')) if x)
    fornitore = {
        'paese': _testo(cedente, 'IdFiscaleIVA/IdPaese') or 'IT',
        'piva': _testo(cedente, 'IdFiscaleIVA/IdCodice'),
        'codice_fiscale': _testo(cedente, 'CodiceFiscale'),
        'denominazione': denominazione or 'Fornitore senza nome',
    }
    if not (fornitore['piva'] or fornitore['codice_fiscale']):
        raise ErroreFattura('il fornitore non ha partita IVA ne\' codice fiscale')

    documenti = [_documento(corpo, fornitore) for corpo in radice.findall('FatturaElettronicaBody')]
    if not documenti:
        raise ErroreFattura('la fattura non ha corpo (FatturaElettronicaBody)')
    return documenti


def _documento(corpo, fornitore):
    generali = corpo.find('DatiGenerali/DatiGeneraliDocumento')
    if generali is None:
        raise ErroreFattura('mancano i dati del documento')
    tipo = _testo(generali, 'TipoDocumento') or 'TD01'
    numero = _testo(generali, 'Numero')
    if not numero:
        raise ErroreFattura('manca il numero della fattura')
    causale = ' '.join((c.text or '').strip() for c in generali.findall('Causale'))

    righe = []
    for linea in corpo.findall('DatiBeniServizi/DettaglioLinee'):
        importo = _decimale(_testo(linea, 'PrezzoTotale')).quantize(CENTESIMO)
        if importo:
            righe.append({
                'numero_linea': int(_decimale(_testo(linea, 'NumeroLinea'), Decimal(len(righe) + 1))),
                'descrizione': _testo(linea, 'Descrizione'),
                'imponibile': importo,
                'aliquota': _decimale(_testo(linea, 'AliquotaIVA')),
            })
    riepiloghi = corpo.findall('DatiBeniServizi/DatiRiepilogo')
    imponibile = sum((_decimale(_testo(r, 'ImponibileImporto')) for r in riepiloghi), Decimal('0'))
    iva = sum((_decimale(_testo(r, 'Imposta')) for r in riepiloghi), Decimal('0'))
    if not riepiloghi:
        imponibile = sum((r['imponibile'] for r in righe), Decimal('0'))
    if not righe:
        righe = [{'numero_linea': i, 'descrizione': f'Imponibile IVA {_testo(r, "AliquotaIVA")}%',
                  'imponibile': _decimale(_testo(r, 'ImponibileImporto')).quantize(CENTESIMO),
                  'aliquota': _decimale(_testo(r, 'AliquotaIVA'))}
                 for i, r in enumerate(riepiloghi, 1)]
    differenza = (imponibile - sum((r['imponibile'] for r in righe), Decimal('0'))).quantize(CENTESIMO)
    if differenza:                                    # arrotondamenti: righe = imponibile
        righe.append({'numero_linea': len(righe) + 1, 'descrizione': 'Arrotondamento',
                      'imponibile': differenza, 'aliquota': Decimal('0')})
    totale = _decimale(_testo(generali, 'ImportoTotaleDocumento'), imponibile + iva)

    scadenze = []
    for p in corpo.findall('DatiPagamento/DettaglioPagamento'):
        scadenza = _testo(p, 'DataScadenzaPagamento')
        scadenze.append({
            'data': _data(scadenza) if scadenza else None,
            'importo': _decimale(_testo(p, 'ImportoPagamento')),
            'modalita': _testo(p, 'ModalitaPagamento')[:4],
            'iban': _testo(p, 'IBAN')[:34],
        })

    # Le note di credito sono costi negativi (alcuni fornitori li scrivono
    # gia' col segno meno: non si gira due volte)
    if tipo in TIPI_NOTA_CREDITO and imponibile + iva > 0:
        imponibile, iva, totale = -imponibile, -iva, -abs(totale)
        for r in righe:
            r['imponibile'] = -r['imponibile']
        for s in scadenze:
            s['importo'] = -abs(s['importo'])

    descrizione = causale or (righe[0]['descrizione'] if righe else '')
    return {
        'fornitore': fornitore,
        'tipo': tipo,
        'numero': numero[:40],
        'data': _data(_testo(generali, 'Data')),
        'descrizione': descrizione[:255],
        'imponibile': imponibile.quantize(CENTESIMO),
        'iva': iva.quantize(CENTESIMO),
        'totale': totale.quantize(CENTESIMO),
        'righe': righe,
        'scadenze': scadenze,
    }
