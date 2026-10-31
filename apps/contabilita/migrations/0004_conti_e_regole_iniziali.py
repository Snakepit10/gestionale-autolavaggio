"""Conti e regole di partenza. Saldi iniziali a zero al 1/1/2026: si
correggono dalla pagina Conti con il saldo vero."""
from datetime import date

from django.db import migrations

CONTI = [('BAPS', 'banca', 1), ('MPS', 'banca', 2), ('Cassa contanti', 'cassa', 3)]

REGOLE = [
    # testo, segno, voce, ordine
    ('Incasso tramite p.o.s', 'entrate', 'N.INCASSI', 10),
    ('Accredito pos', 'entrate', 'N.INCASSI', 11),
    ('Versamento contanti', 'entrate', 'N.GIROCONTO', 15),
    ('Nexi', 'uscite', 'CV.COMMISSIONI', 20),
    ('Commissioni', 'uscite', 'CF.BANCA', 30),
    ('Canone', 'uscite', 'CF.BANCA', 31),
    ('Spese tenuta conto', 'uscite', 'CF.BANCA', 32),
    ('Imposta di bollo', 'uscite', 'I.IMPOSTE', 35),
    ('Interessi', 'uscite', 'OF.INTERESSI', 40),
    ('Rimborso finanziamento', 'uscite', 'N.FINANZIAMENTI', 50),
    ('Rata mutuo', 'uscite', 'N.FINANZIAMENTI', 51),
    ('Emolumenti', 'uscite', 'N.STIPENDI', 60),
    ('Stipendio', 'uscite', 'N.STIPENDI', 61),
]


def avanti(apps, schema_editor):
    Conto = apps.get_model('contabilita', 'ContoFinanziario')
    Regola = apps.get_model('contabilita', 'RegolaMovimento')
    VoceConto = apps.get_model('contabilita', 'VoceConto')
    for nome, tipo, ordine in CONTI:
        Conto.objects.get_or_create(nome=nome, defaults={
            'tipo': tipo, 'ordine': ordine, 'data_saldo_iniziale': date(2026, 1, 1)})
    voci = {v.codice: v for v in VoceConto.objects.all()}
    for testo, segno, codice, ordine in REGOLE:
        if codice in voci:
            Regola.objects.get_or_create(testo=testo, segno=segno, defaults={'voce': voci[codice], 'ordine': ordine})


def indietro(apps, schema_editor):
    apps.get_model('contabilita', 'RegolaMovimento').objects.filter(testo__in=[r[0] for r in REGOLE]).delete()
    apps.get_model('contabilita', 'ContoFinanziario').objects.filter(nome__in=[c[0] for c in CONTI],
                                                                    movimenti__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0003_prima_nota'),
    ]

    operations = [
        migrations.RunPython(avanti, indietro),
    ]
