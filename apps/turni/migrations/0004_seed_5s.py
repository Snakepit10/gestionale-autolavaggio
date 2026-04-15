from django.db import migrations


CATEGORIE_5S = [
    {
        'nome': 'Strumenti/Materiali',
        'icona': 'bi-wrench',
        'ordine': 1,
        'esiti': [
            ('ok', 'OK', 'success', 1),
            ('mancante', 'Mancante', 'danger', 2),
            ('fuori_posto', 'Fuori posto', 'warning', 3),
            ('da_sostituire', 'Da sostituire', 'danger', 4),
            ('da_pulire', 'Da pulire', 'info', 5),
            ('non_conforme', 'Non conforme', 'danger', 6),
            ('in_eccesso', 'In eccesso', 'warning', 7),
            ('na', 'N/A', 'secondary', 8),
        ],
    },
    {
        'nome': 'Pulizia (Seiso)',
        'icona': 'bi-droplet',
        'ordine': 2,
        'esiti': [
            ('ok', 'OK', 'success', 1),
            ('da_pulire', 'Da pulire', 'warning', 2),
            ('non_conforme', 'Non conforme', 'danger', 3),
            ('na', 'N/A', 'secondary', 4),
        ],
    },
    {
        'nome': 'Ordine (Seiton)',
        'icona': 'bi-box-seam',
        'ordine': 3,
        'esiti': [
            ('ok', 'OK', 'success', 1),
            ('fuori_posto', 'Fuori posto', 'warning', 2),
            ('non_conforme', 'Non conforme', 'danger', 3),
            ('na', 'N/A', 'secondary', 4),
        ],
    },
    {
        'nome': 'Eliminazione (Seiri)',
        'icona': 'bi-trash',
        'ordine': 4,
        'esiti': [
            ('ok', 'OK', 'success', 1),
            ('non_conforme', 'Non conforme', 'danger', 2),
            ('in_eccesso', 'In eccesso', 'warning', 3),
            ('presente_non_necessario', 'Presente ma non necessario', 'info', 4),
            ('na', 'N/A', 'secondary', 5),
        ],
    },
    {
        'nome': 'Standardizzazione (Seiketsu)',
        'icona': 'bi-clipboard-check',
        'ordine': 5,
        'esiti': [
            ('ok', 'OK', 'success', 1),
            ('mancante', 'Mancante', 'danger', 2),
            ('non_conforme', 'Non conforme', 'danger', 3),
            ('na', 'N/A', 'secondary', 4),
        ],
    },
]


def seed_5s(apps, schema_editor):
    CategoriaChecklist = apps.get_model('turni', 'CategoriaChecklist')
    EsitoChecklist = apps.get_model('turni', 'EsitoChecklist')
    ChecklistItem = apps.get_model('turni', 'ChecklistItem')

    prima_cat = None
    for cat_data in CATEGORIE_5S:
        cat, _ = CategoriaChecklist.objects.get_or_create(
            nome=cat_data['nome'],
            defaults={'icona': cat_data['icona'], 'ordine': cat_data['ordine']},
        )
        if prima_cat is None:
            prima_cat = cat
        for codice, nome, colore, ordine in cat_data['esiti']:
            EsitoChecklist.objects.get_or_create(
                categoria=cat, codice=codice,
                defaults={'nome': nome, 'colore': colore, 'ordine': ordine},
            )

    # Assegna gli item esistenti alla prima categoria
    if prima_cat:
        ChecklistItem.objects.filter(categoria__isnull=True).update(categoria=prima_cat)


def reverse_seed(apps, schema_editor):
    CategoriaChecklist = apps.get_model('turni', 'CategoriaChecklist')
    ChecklistItem = apps.get_model('turni', 'ChecklistItem')
    ChecklistItem.objects.all().update(categoria=None)
    CategoriaChecklist.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('turni', '0003_checklist_5s'),
    ]

    operations = [
        migrations.RunPython(seed_5s, reverse_seed),
    ]
