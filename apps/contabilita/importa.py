"""Registrazione delle fatture dei fornitori lette da fatturapa.py, sia
dalla PEC sia dal caricamento a mano. Idempotente: la stessa fattura
(P.IVA, tipo, numero, data) non entra due volte."""
import re
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction

from apps.magazzino.models import Fornitore

from .fatturapa import ErroreFattura, estrai_xml, leggi_fattura
from .models import FatturaPassiva, RegolaFornitore, RigaFatturaPassiva, ScadenzaFattura


@dataclass
class Esito:
    nuove: list = field(default_factory=list)
    doppie: list = field(default_factory=list)
    errori: list = field(default_factory=list)

    def testo(self):
        parti = [f'{len(self.nuove)} nuov{"a" if len(self.nuove) == 1 else "e"}']
        if self.doppie:
            parti.append(f'{len(self.doppie)} gia\' presenti')
        if self.errori:
            parti.append(f'{len(self.errori)} con errori')
        return ', '.join(parti)


def importa_file(nome, dati, origine='upload', esito=None):
    esito = esito if esito is not None else Esito()
    try:
        file_xml = estrai_xml(nome, dati)
    except ErroreFattura as e:
        esito.errori.append(str(e))
        return esito
    for nome_xml, xml in file_xml:
        try:
            documenti = leggi_fattura(xml)
        except ErroreFattura as e:
            esito.errori.append(f'{nome_xml}: {e}')
            continue
        for doc in documenti:
            fattura, nuova = registra(doc, nome_xml, xml, origine)
            (esito.nuove if nuova else esito.doppie).append(fattura)
    return esito


def _codice(testo):
    codice = re.sub(r'[^0-9A-Z]', '', (testo or '').upper())
    return codice[2:] if codice.startswith('IT') and len(codice) == 13 else codice


def trova_fornitore(dati):
    """Fornitore dell'anagrafica con la stessa P.IVA (o codice fiscale);
    se manca si crea."""
    cercati = {c for c in (_codice(dati['piva']), _codice(dati['codice_fiscale'])) if c}
    for f in Fornitore.objects.exclude(partita_iva=''):
        if _codice(f.partita_iva) in cercati:
            return f
    return Fornitore.objects.create(ragione_sociale=dati['denominazione'][:150],
                                    partita_iva=dati['piva'] or dati['codice_fiscale'])


def identificativo(doc):
    f = doc['fornitore']
    return f"{f['paese']}{_codice(f['piva'] or f['codice_fiscale'])}|{doc['tipo']}|{doc['numero']}|{doc['data']:%Y-%m-%d}"


def registra(doc, nome_file, xml, origine):
    chiave = identificativo(doc)
    esistente = FatturaPassiva.objects.filter(identificativo=chiave).first()
    if esistente:
        return esistente, False
    mese = doc['data'].replace(day=1)
    try:
        with transaction.atomic():
            fornitore = trova_fornitore(doc['fornitore'])
            fattura = FatturaPassiva.objects.create(
                fornitore=fornitore, tipo_documento=doc['tipo'], numero=doc['numero'],
                data=doc['data'], descrizione=doc['descrizione'], imponibile=doc['imponibile'],
                iva=doc['iva'], totale=doc['totale'], competenza_dal=mese, competenza_al=mese,
                origine=origine, identificativo=chiave, file_nome=nome_file[:200], file_xml=xml)
            regola = RegolaFornitore.objects.filter(fornitore=fornitore).first()
            RigaFatturaPassiva.objects.bulk_create([
                RigaFatturaPassiva(
                    fattura=fattura, numero_linea=r['numero_linea'], descrizione=r['descrizione'],
                    imponibile=r['imponibile'], aliquota_iva=r['aliquota'],
                    voce=regola.voce if regola else None,
                    ripartizione=regola.ripartizione if regola else {})
                for r in doc['righe']])
            scadenze = doc['scadenze'] or ([{'data': doc['data'], 'importo': doc['totale'],
                                             'modalita': '', 'iban': ''}] if doc['totale'] > 0 else [])
            ScadenzaFattura.objects.bulk_create([ScadenzaFattura(fattura=fattura, **s) for s in scadenze])
            fattura.aggiorna_classificata()
    except IntegrityError:                            # importata nel frattempo (PEC + caricamento)
        return FatturaPassiva.objects.get(identificativo=chiave), False
    return fattura, True
