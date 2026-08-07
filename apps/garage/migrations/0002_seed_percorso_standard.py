# Seed del percorso standard, dei tipi servizio e dei badge.
# Data migration REVERSIBILE: il reverse elimina solo i dati seminati
# (per slug/nome noti), senza toccare eventuali dati creati dopo.
from django.db import migrations

# (slug, nome, punti, validita_giorni, badge_nome, badge_icona, ordine)
TIPI = [
    ('lavaggio_esterno', 'Lavaggio esterno', 3, 0, '', 'bi-star', 10),
    ('lavaggio_interno', 'Lavaggio interno', 3, 0, '', 'bi-star', 20),
    ('lavaggio_sottoscocca', 'Lavaggio sottoscocca', 4, 0,
     'Sottoscocca protetto', 'bi-shield-check', 30),
    ('smacchiatura_sedili', 'Smacchiatura sedili', 6, 0,
     'Interni come nuovi', 'bi-stars', 40),
    ('pulizia_sterzo_comandi', 'Pulizia approfondita sterzo e comandi', 4, 0,
     '', 'bi-star', 50),
    ('grafitaggio', 'Grafitaggio', 6, 365, '', 'bi-star', 60),
    ('sanificazione_abitacolo', 'Sanificazione abitacolo', 5, 180,
     'Abitacolo sano', 'bi-heart-pulse', 70),
    ('ripristino_plastiche', 'Ripristino plastiche', 5, 150, '', 'bi-star', 80),
    ('ripristino_cromature', 'Ripristino cromature', 4, 180, '', 'bi-star', 90),
    ('pulizia_vano_motore', 'Pulizia vano motore', 5, 0,
     'Motore brillante', 'bi-gear', 100),
    ('coating_ceramico', 'Coating ceramico', 12, 450,
     'Ceramic Club', 'bi-gem', 110),
    ('nano_tessuti_pelle', 'Nanotecnologia tessuti/pelle', 8, 270,
     '', 'bi-star', 120),
    ('nano_gomme', 'Nanotecnologia gomme', 8, 270, '', 'bi-star', 130),
    ('self_service', 'Lavaggio self service', 2, 0, '', 'bi-droplet', 200),
]

# (numero, nome, premio_gettoni, badge_nome, [slug item...])
LIVELLI = [
    (1, 'Base pulita', 2, 'Base pulita',
     ['lavaggio_esterno', 'lavaggio_interno', 'lavaggio_sottoscocca']),
    (2, 'Cura profonda', 4, 'Cura profonda',
     ['smacchiatura_sedili', 'pulizia_sterzo_comandi', 'grafitaggio',
      'sanificazione_abitacolo']),
    (3, 'Ripristino', 6, 'Ripristino totale',
     ['ripristino_plastiche', 'ripristino_cromature', 'pulizia_vano_motore']),
    (4, 'Protezione', 10, 'Club Auto Perfetta',
     ['coating_ceramico', 'nano_tessuti_pelle', 'nano_gomme']),
]

NOME_PERCORSO = 'Percorso standard'


def seed(apps, schema_editor):
    Percorso = apps.get_model('garage', 'Percorso')
    LivelloPercorso = apps.get_model('garage', 'LivelloPercorso')
    TipoServizioGarage = apps.get_model('garage', 'TipoServizioGarage')
    ItemLivello = apps.get_model('garage', 'ItemLivello')
    ImpostazioniGarage = apps.get_model('garage', 'ImpostazioniGarage')

    tipi = {}
    for slug, nome, punti, validita, badge, icona, ordine in TIPI:
        tipi[slug], _ = TipoServizioGarage.objects.get_or_create(
            slug=slug,
            defaults={'nome': nome, 'punti_salute': punti,
                      'validita_giorni': validita, 'badge_nome': badge,
                      'badge_icona': icona, 'ordine': ordine, 'attivo': True})

    percorso, _ = Percorso.objects.get_or_create(
        nome=NOME_PERCORSO, defaults={'predefinito': True, 'attivo': True})

    for numero, nome, premio, badge, slugs in LIVELLI:
        livello, _ = LivelloPercorso.objects.get_or_create(
            percorso=percorso, numero=numero,
            defaults={'nome': nome, 'premio_gettoni': premio,
                      'badge_nome': badge, 'badge_icona': 'bi-award'})
        for i, slug in enumerate(slugs):
            ItemLivello.objects.get_or_create(
                livello=livello, tipo_servizio=tipi[slug],
                defaults={'ordine': i * 10,
                          'soddisfatto_da_self': slug == 'lavaggio_esterno'})

    # Singleton impostazioni con i default JSON valorizzati
    if not ImpostazioniGarage.objects.exists():
        ImpostazioniGarage.objects.create(
            pk=1, streak_traguardi={'5': 1, '10': 3, '20': 6},
            giorni_avviso_scadenza=[30, 7])


def unseed(apps, schema_editor):
    Percorso = apps.get_model('garage', 'Percorso')
    TipoServizioGarage = apps.get_model('garage', 'TipoServizioGarage')
    # CASCADE elimina livelli e item del percorso seminato
    Percorso.objects.filter(nome=NOME_PERCORSO).delete()
    TipoServizioGarage.objects.filter(
        slug__in=[t[0] for t in TIPI]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('garage', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
