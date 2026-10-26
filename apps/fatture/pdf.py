"""PDF delle schede Fatture (stile "fascia scura" col logo MasterWash).

Le funzioni `elementi_*` restituiscono i flowable reportlab del corpo;
`genera_pdf` aggiunge intestazione, piede con i dati aziendali e
restituisce i byte del documento.
"""
import io
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

DATI_AZIENDA = [
    'MASTERWASH',
    'Autolavaggio di Lombardo Daniele',
    'Via Palma snc, 92027 Licata (AG)',
    'P.IVA 02703400842 - Codice Univoco M5UXCR1',
]
LOGO = settings.BASE_DIR / 'static' / 'img' / 'masterwash-logo-bianco.png'
LOGO_RATIO = 198 / 831  # altezza / larghezza del PNG

NAVY = colors.HexColor('#0e2233')
NAVY2 = colors.HexColor('#0e3350')
CIANO = colors.HexColor('#6fd0f9')
BLU = colors.HexColor('#1b6fa3')
GRIGIO = colors.HexColor('#64748b')
LINEA = colors.HexColor('#dbe4ec')
ZEBRA = colors.HexColor('#f4f8fb')
ROSSO = colors.HexColor('#c0392b')

PAGINA = landscape(A4)
MARGINE = 14 * mm
ZERO = Decimal('0')

# Colonne del dettaglio ordini/voci (larghezza utile ~269mm)
INTEST_DETTAGLIO = ['Ordine', 'Data', 'Tipo auto', 'Servizi', 'Targa',
                    'Matricola', 'Nota', 'Importo']
LARGHEZZE_DETTAGLIO = [16 * mm, 17 * mm, 38 * mm, None, 22 * mm, 20 * mm,
                       34 * mm, 26 * mm]


def eur(valore):
    return ('€ %.2f' % (valore or ZERO)).replace('.', ',')


def _p(testo, **stile):
    base = dict(fontName='Helvetica', fontSize=8, leading=10,
                textColor=NAVY)
    base.update(stile)
    return Paragraph(testo, ParagraphStyle('p', **base))


def _t(valore):
    """Testo utente in cella (escape, '-' se vuoto)."""
    return _p(escape(str(valore or '-')))


def _servizi(ordine):
    return ', '.join(
        i.servizio_prodotto.titolo for i in ordine.items.all()
    ) or (ordine.nota or '-')


def _dati_cliente(cliente):
    if not cliente:
        return 'Senza cliente', ''
    extra = []
    if cliente.partita_iva:
        extra.append(f'<font color="#64748b">P.IVA</font> '
                     f'{escape(cliente.partita_iva)}')
    if cliente.telefono:
        extra.append(f'<font color="#64748b">Tel.</font> '
                     f'{escape(cliente.telefono)}')
    localita = ' '.join(x for x in [cliente.cap, cliente.citta] if x)
    indirizzo = ', '.join(x for x in [cliente.indirizzo, localita] if x)
    if indirizzo:
        extra.append(escape(indirizzo))
    return cliente.nome_completo, '<br/>'.join(extra)


# --------------------------- blocchi grafici ---------------------------

def box_cliente(cliente, etichetta, importo, colore=ROSSO):
    """Riquadro col cliente e l'importo principale in evidenza."""
    nome, extra = _dati_cliente(cliente)
    t = Table([[
        _p('<font size=7 color="#64748b">CLIENTE</font><br/>'
           f'<b><font size=12>{escape(nome)}</font></b>', leading=15),
        _p(extra, leading=11),
        _p(f'<font size=7 color="#64748b">{etichetta}</font><br/>'
           f'<b><font size=15 color="{colore.hexval().replace("0x", "#")}">'
           f'{eur(importo)}</font></b>', leading=18, alignment=2),
    ]], colWidths=[95 * mm, 110 * mm, None])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), ZEBRA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBEFORE', (0, 0), (0, 0), 3, BLU),
    ]))
    return [t, Spacer(1, 7 * mm)]


def tabella(righe, larghezze, totali, barra=None, allinea_destra=(-1,)):
    """Tabella con intestazione blu, righe alternate e `totali` righe
    finali (etichetta, importo, colore) in grassetto.

    `barra` = (titolo, sotto, destra): riga blu notte in cima (fattura o
    cliente) che si ripete con l'intestazione se la tabella va a capo
    pagina. Le tabelle corte non vengono spezzate.
    """
    corpo = []
    if barra:
        titolo, sotto, destra = barra
        testo = f'<b><font size=10 color="white">{titolo}</font></b>'
        if sotto:
            testo += f'  <font color="#6fd0f9">{sotto}</font>'
        corpo.append([_p(testo)] + [''] * (len(larghezze) - 3) + [
            _p(f'<font color="#6fd0f9">{destra}</font>', alignment=2), ''])
    h = len(corpo)  # riga dell'intestazione colonne
    corpo += [r[:] for r in righe]
    n = len(corpo)  # prima riga dei totali
    for etichetta, valore, _ in totali:
        corpo.append([''] * (len(larghezze) - 2) + [etichetta, valore])
    t = Table(corpo, colWidths=larghezze, repeatRows=h + 1)
    stile = [
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 8),
        ('TEXTCOLOR', (0, 0), (-1, -1), NAVY),
        ('FONT', (0, h), (-1, h), 'Helvetica-Bold', 7),
        ('TEXTCOLOR', (0, h), (-1, h), BLU),
        ('LINEBELOW', (0, h), (-1, h), 0.8, BLU),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if barra:
        stile += [
            ('SPAN', (0, 0), (-3, 0)), ('SPAN', (-2, 0), (-1, 0)),
            ('BACKGROUND', (0, 0), (-1, 0), NAVY2),
            ('TOPPADDING', (0, 0), (-1, 0), 5),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ]
    for col in allinea_destra:
        stile.append(('ALIGN', (col, h), (col, -1), 'RIGHT'))
    for i in range(h + 2, n, 2):
        stile.append(('BACKGROUND', (0, i), (-1, i), ZEBRA))
    if totali:
        stile += [
            ('LINEABOVE', (0, n), (-1, n), 0.8, NAVY),
            ('FONT', (-2, n), (-1, -1), 'Helvetica-Bold', 8.5),
            ('ALIGN', (-2, n), (-1, -1), 'RIGHT'),
        ]
        for i, (_, _, colore) in enumerate(totali):
            if colore:
                stile.append(('TEXTCOLOR', (-2, n + i), (-1, n + i), colore))
    t.setStyle(TableStyle(stile))
    return [KeepTogether(t) if len(corpo) <= 15 else t, Spacer(1, 6 * mm)]


def box_riepilogo(voci):
    """Riquadro finale a destra: [(etichetta, valore, colore), ...]."""
    t = Table([[v[0] for v in voci], [v[1] for v in voci]],
              colWidths=[45 * mm] * len(voci), hAlign='RIGHT')
    stile = [
        ('FONT', (0, 0), (-1, 0), 'Helvetica', 7),
        ('TEXTCOLOR', (0, 0), (-1, 0), GRIGIO),
        ('FONT', (0, 1), (-1, 1), 'Helvetica-Bold', 12),
        ('TEXTCOLOR', (0, 1), (-1, 1), NAVY),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('BOX', (0, 0), (-1, -1), 0.8, LINEA),
        ('BACKGROUND', (0, 0), (-1, -1), ZEBRA),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]
    for i, (_, _, colore) in enumerate(voci):
        if colore:
            stile.append(('TEXTCOLOR', (i, 1), (i, 1), colore))
    t.setStyle(TableStyle(stile))
    return [Spacer(1, 2 * mm), KeepTogether(t)]


def _righe_ordini_voci(ordini, voci, importo_ordine):
    righe = [INTEST_DETTAGLIO]
    for o in ordini:
        righe.append([
            f'#{o.numero_breve}',
            timezone.localtime(o.data_ora).strftime('%d/%m/%y'),
            _t(o.tipo_auto), _t(_servizi(o)), _t(o.fattura_targa),
            _t(o.fattura_matricola), _t(o.fattura_nota),
            eur(importo_ordine(o)),
        ])
    for v in voci:
        righe.append([
            'voce', v.data.strftime('%d/%m/%y'),
            _t(v.tipo_auto), _t(v.descrizione), _t(v.targa),
            _t(v.matricola), _t(v.nota), eur(v.importo),
        ])
    return righe


def _info_cliente_barra(cliente):
    if not cliente:
        return 'Senza cliente', ''
    extra = [x for x in [cliente.telefono,
                         cliente.partita_iva and f'P.IVA {cliente.partita_iva}']
             if x]
    return escape(cliente.nome_completo), escape(' - '.join(extra))


# ------------------------------ sezioni ------------------------------

def elementi_da_fatturare(gruppi, per_cliente):
    """Ordini e voci manuali in attesa, un blocco per cliente."""
    el = []
    totale = sum((g['totale'] for g in gruppi), ZERO)
    n_elementi = sum(g['n_elementi'] for g in gruppi)
    if per_cliente:
        el += box_cliente(gruppi[0]['cliente'], 'DA FATTURARE', totale, BLU)
    for g in gruppi:
        righe = _righe_ordini_voci(
            g['ordini'], g['voci'], lambda o: o.totale_finale or ZERO)
        barra = None if per_cliente else (
            *_info_cliente_barra(g['cliente']), eur(g['totale']))
        el += tabella(righe, LARGHEZZE_DETTAGLIO,
                      [('Totale', eur(g['totale']), None)], barra)
    riepilogo = [('Elementi', str(n_elementi), None),
                 ('Totale da fatturare', eur(totale), BLU)]
    if not per_cliente:
        riepilogo.insert(0, ('Clienti', str(len(gruppi)), None))
    return el + box_riepilogo(riepilogo)


def elementi_fatture_sintesi(gruppi, da_pagare):
    """Elenco fatture emesse raggruppate per cliente (una riga a fattura)."""
    el = []
    intest = ['Numero', 'Data', 'Ragione sociale', 'Elementi', 'Stato',
              'Saldo', 'Totale']
    larghezze = [28 * mm, 22 * mm, None, 22 * mm, 28 * mm, 30 * mm, 30 * mm]
    for g in gruppi:
        righe = [intest]
        for f in g['fatture']:
            righe.append([
                f.numero, f.data.strftime('%d/%m/%y'), _t(f.ragione_sociale),
                str(len(f.ordini.all()) + len(f.righe.all())),
                f.get_stato_display(), eur(f.saldo_dovuto), eur(f.totale),
            ])
        totali = [('Totale', eur(g['totale']), None)]
        if da_pagare and g['saldo'] != g['totale']:
            totali.append(('Da incassare', eur(g['saldo']), ROSSO))
        destra = f'da incassare {eur(g["saldo"])}' if da_pagare else ''
        el += tabella(righe, larghezze, totali,
                      (*_info_cliente_barra(g['cliente']), destra),
                      allinea_destra=(3, 5, 6))
    return el + box_riepilogo(_riepilogo_fatture(gruppi, da_pagare, True))


def elementi_fatture_dettaglio(gruppi, da_pagare):
    """Riepilogo di un cliente: ogni fattura col dettaglio delle righe."""
    saldo = sum((g['saldo'] for g in gruppi), ZERO)
    totale = sum((g['totale'] for g in gruppi), ZERO)
    el = box_cliente(gruppi[0]['cliente'],
                     'DA INCASSARE' if da_pagare else 'TOTALE PAGATO',
                     saldo if da_pagare else totale,
                     ROSSO if da_pagare else BLU)
    for g in gruppi:
        for f in g['fatture']:
            righe = _righe_ordini_voci(
                f.ordini.all(), f.righe.all(), lambda o: o.importo_in_fattura)
            totali = [('Totale fattura', eur(f.totale), None)]
            if f.saldo_dovuto != f.totale:
                totali.append(('Ancora da pagare', eur(f.saldo_dovuto),
                               ROSSO))
            sotto = (f'del {f.data.strftime("%d/%m/%Y")} - '
                     f'{escape(f.ragione_sociale)}')
            el += tabella(righe, LARGHEZZE_DETTAGLIO, totali,
                          (f'Fattura {escape(f.numero)}', sotto,
                           f.get_stato_display().upper()))
    return el + box_riepilogo(_riepilogo_fatture(gruppi, da_pagare, False))


def _riepilogo_fatture(gruppi, da_pagare, con_clienti):
    voci = [('Fatture', str(sum(len(g['fatture']) for g in gruppi)), None),
            ('Totale fatturato',
             eur(sum((g['totale'] for g in gruppi), ZERO)), None)]
    if con_clienti:
        voci.insert(0, ('Clienti', str(len(gruppi)), None))
    if da_pagare:
        voci.append(('Da incassare',
                     eur(sum((g['saldo'] for g in gruppi), ZERO)), ROSSO))
    return voci


def nessun_elemento():
    return [_p('<font color="#64748b">Nessun elemento.</font>', fontSize=10)]


# ------------------------------ documento ------------------------------

def genera_pdf(titolo, elementi):
    """Impagina `elementi` con intestazione (logo, dati aziendali, titolo)
    e piede di pagina; restituisce i byte del PDF."""
    larghezza, altezza = PAGINA
    generato = timezone.localtime().strftime('%d/%m/%Y alle %H:%M')

    def piede(c, doc):
        c.saveState()
        c.setStrokeColor(LINEA)
        c.line(MARGINE, 10 * mm, larghezza - MARGINE, 10 * mm)
        c.setFont('Helvetica', 7)
        c.setFillColor(GRIGIO)
        c.drawString(MARGINE, 6.5 * mm, ' - '.join(DATI_AZIENDA))
        c.drawRightString(larghezza - MARGINE, 6.5 * mm,
                          'Pagina %d' % doc.page)
        c.restoreState()

    def fascia(c, alta, logo_w):
        c.setFillColor(NAVY)
        c.rect(0, altezza - alta, larghezza, alta, stroke=0, fill=1)
        c.setFillColor(CIANO)
        c.rect(0, altezza - alta - 1.2 * mm, larghezza, 1.2 * mm,
               stroke=0, fill=1)
        logo_h = logo_w * LOGO_RATIO
        if LOGO.exists():
            c.drawImage(str(LOGO), MARGINE, altezza - (alta + logo_h) / 2,
                        logo_w, logo_h, mask='auto')

    def prima_pagina(c, doc):
        c.saveState()
        fascia(c, 34 * mm, 70 * mm)
        # dati aziendali al centro
        x = MARGINE + 82 * mm
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(x, altezza - 11 * mm, DATI_AZIENDA[0])
        c.setFont('Helvetica', 8)
        c.setFillColor(colors.HexColor('#cbe2ee'))
        for i, riga in enumerate(DATI_AZIENDA[1:]):
            c.drawString(x, altezza - (15.5 + 4.2 * i) * mm, riga)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 16)
        c.drawRightString(larghezza - MARGINE, altezza - 15 * mm,
                          titolo.upper())
        c.setFont('Helvetica', 8.5)
        c.setFillColor(CIANO)
        c.drawRightString(larghezza - MARGINE, altezza - 21 * mm,
                          f'Generato il {generato}')
        c.restoreState()
        piede(c, doc)

    def altre_pagine(c, doc):
        c.saveState()
        fascia(c, 14 * mm, 38 * mm)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 10)
        c.drawRightString(larghezza - MARGINE, altezza - 8.5 * mm,
                          titolo.upper())
        c.restoreState()
        piede(c, doc)

    def cornice(alto):
        return Frame(MARGINE, 16 * mm, larghezza - 2 * MARGINE,
                     altezza - alto - 16 * mm, leftPadding=0,
                     rightPadding=0, topPadding=0, bottomPadding=0)

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=PAGINA, title=titolo,
                          author=DATI_AZIENDA[0])
    # Dalla seconda pagina la fascia e' bassa: meno margine in alto
    doc.addPageTemplates([
        PageTemplate(id='prima', frames=[cornice(42 * mm)],
                     onPage=prima_pagina, autoNextPageTemplate='altre'),
        PageTemplate(id='altre', frames=[cornice(22 * mm)],
                     onPage=altre_pagine),
    ])
    doc.build(elementi)
    return buffer.getvalue()
