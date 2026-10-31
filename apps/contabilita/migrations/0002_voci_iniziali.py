"""Piano dei conti iniziale, sulle voci del Financial Model. Le
ripartizioni sui centri si impostano poi dalla pagina Piano dei conti:
qui sono assegnate solo quelle ovvie."""
from django.db import migrations

VOCI = [
    # codice, nome, sezione, flusso, ripartizione
    ('R.ALTRI', 'Altri ricavi', 'ricavi', 'operativo', {}),

    ('CV.ACQUA', 'Acqua', 'costi_variabili', 'operativo', {}),
    ('CV.ENERGIA', 'Energia elettrica', 'costi_variabili', 'operativo', {}),
    ('CV.GAS', 'Gas e combustibili', 'costi_variabili', 'operativo', {}),
    ('CV.CHIMICI', 'Prodotti chimici', 'costi_variabili', 'operativo', {}),
    ('CV.CONSUMO', 'Materiale di consumo', 'costi_variabili', 'operativo', {'servito': 100}),
    ('CV.PRODOTTI', 'Prodotti per la rivendita', 'costi_variabili', 'operativo', {'prodotti': 100}),
    ('CV.COMMISSIONI', 'Commissioni POS e pagamenti', 'costi_variabili', 'operativo', {}),

    ('CF.AFFITTO', 'Affitto e locazioni', 'costi_fissi', 'operativo', {}),
    ('CF.MANUTENZIONE', 'Manutenzione impianti', 'costi_fissi', 'operativo', {}),
    ('CF.ASSICURAZIONI', 'Assicurazioni', 'costi_fissi', 'operativo', {}),
    ('CF.TELEFONO', 'Internet e telefono', 'costi_fissi', 'operativo', {}),
    ('CF.CONSULENZE', 'Commercialista e consulenze', 'costi_fissi', 'operativo', {}),
    ('CF.SOFTWARE', 'Software e servizi online', 'costi_fissi', 'operativo', {}),
    ('CF.MARKETING', 'Pubblicita\' e marketing', 'costi_fissi', 'operativo', {}),
    ('CF.VIGILANZA', 'Vigilanza e metronotte', 'costi_fissi', 'operativo', {}),
    ('CF.RIFIUTI', 'Smaltimento rifiuti', 'costi_fissi', 'operativo', {}),
    ('CF.CARBURANTE', 'Carburante e automezzi', 'costi_fissi', 'operativo', {}),
    ('CF.BANCA', 'Spese bancarie', 'costi_fissi', 'operativo', {}),
    ('CF.ALTRI', 'Altri costi', 'costi_fissi', 'operativo', {}),

    ('P.ALTRI', 'Altri costi del personale (divise, corsi, consulente paghe)', 'personale', 'operativo', {}),

    ('A.AMMORTAMENTI', 'Ammortamenti', 'ammortamenti', 'investimenti', {}),
    ('OF.INTERESSI', 'Interessi passivi e oneri finanziari', 'oneri_finanziari', 'finanziario', {}),
    ('I.IMPOSTE', 'Imposte e tasse', 'imposte', 'operativo', {}),

    ('N.INCASSI', 'Incassi (POS, clienti, monete online)', 'non_economico', 'operativo', {}),
    ('N.FORNITORI', 'Pagamento fornitori', 'non_economico', 'operativo', {}),
    ('N.STIPENDI', 'Stipendi, contributi e ritenute', 'non_economico', 'operativo', {}),
    ('N.IVA', 'IVA (liquidazioni)', 'non_economico', 'operativo', {}),
    ('N.INVESTIMENTI', 'Acquisto impianti e attrezzature', 'non_economico', 'investimenti', {}),
    ('N.FINANZIAMENTI', 'Finanziamenti (erogazioni e rate)', 'non_economico', 'finanziario', {}),
    ('N.TITOLARE', 'Prelievi e versamenti del titolare', 'non_economico', 'finanziario', {}),
    ('N.GIROCONTO', 'Giroconto tra conti e versamenti contanti', 'non_economico', 'giroconto', {}),
]


def avanti(apps, schema_editor):
    VoceConto = apps.get_model('contabilita', 'VoceConto')
    for ordine, (codice, nome, sezione, flusso, ripartizione) in enumerate(VOCI, 1):
        VoceConto.objects.get_or_create(codice=codice, defaults={
            'nome': nome, 'sezione': sezione, 'flusso': flusso,
            'ripartizione': ripartizione, 'ordine': ordine * 10})


def indietro(apps, schema_editor):
    apps.get_model('contabilita', 'VoceConto').objects.filter(codice__in=[v[0] for v in VOCI]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilita', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(avanti, indietro),
    ]
