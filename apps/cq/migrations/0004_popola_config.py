"""
Data migration: popola CategoriaZona, ZonaConfig, CategoriaDifetto,
TipoDifettoConfig e ZonaDifettoMapping con i dati predefiniti.
Sicura da eseguire anche su DB vuoto: non modifica DifettoCQ esistenti
(i codici già presenti corrispondono ai codici appena creati).
"""
from django.db import migrations


# ---------------------------------------------------------------------------
# Dati statici (copiati da logic.py v1 — non importare da lì per evitare
# dipendenze rotte se il file viene modificato dopo la migrazione)
# ---------------------------------------------------------------------------

CATEGORIE_ZONA = [
    (0, 'Esterno'),
    (1, 'Interni — Plastiche e Sedili'),
    (2, 'Interni — Moquette e Tappeti'),
    (3, 'Vetri interni'),
    (4, 'Vani'),
]

ZONE = [
    # (ordine, codice, nome, cat_ordine, produttore, catena)
    (0,  'carrozzeria_aloni_residui',    'Carrozzeria — aloni / gocce / residui', 0, 'post1', ['post2']),
    (1,  'parabrezza_esterno',           'Parabrezza esterno',                    0, 'post1', ['post2', 'post4']),
    (2,  'lunotto_esterno',              'Lunotto esterno',                       0, 'post1', ['post2', 'post4']),
    (3,  'vetri_laterali_esterni',       'Vetri laterali esterni',                0, 'post1', ['post2', 'post4']),
    (4,  'specchietti_esterni',          'Specchietti esterni sx e dx',           0, 'post1', ['post2', 'post4']),
    (5,  'cerchi',                       'Cerchi',                                0, 'post1', []),
    (6,  'passaruota',                   'Passaruota',                            0, 'post1', []),
    (7,  'gomme_nero_gomme',             'Gomme — nero gomme',                    0, 'post3', []),
    (8,  'paraurti',                     'Paraurti anteriore e posteriore',       0, 'post1', ['post2']),
    (0,  'cruscotto_plancia',            'Cruscotto e plancia',                   1, 'post4', []),
    (1,  'bocchette_aria',               'Bocchette aria',                        1, 'post4', []),
    (2,  'tunnel_centrale',              'Tunnel centrale',                       1, 'post4', []),
    (3,  'montanti',                     'Montanti',                              1, 'post4', []),
    (4,  'battitacchi',                  'Battitacchi',                           1, 'post4', []),
    (5,  'sedile_guidatore',             'Sedile guidatore',                      1, 'post4', []),
    (6,  'sedile_passeggero',            'Sedile passeggero',                     1, 'post4', []),
    (7,  'sedili_posteriori',            'Sedili posteriori',                     1, 'post4', []),
    (8,  'vani_portiere',                'Vani portiere',                         1, 'post4', []),
    (9,  'pannelli_portiere',            'Pannelli portiere',                     1, 'post4', []),
    (0,  'moquette_ant_sx',              'Moquette anteriore sx',                 2, 'post3', []),
    (1,  'moquette_ant_dx',              'Moquette anteriore dx',                 2, 'post3', []),
    (2,  'moquette_post_sx',             'Moquette posteriore sx',                2, 'post3', []),
    (3,  'moquette_post_dx',             'Moquette posteriore dx',                2, 'post3', []),
    (4,  'tappeto_guidatore',            'Tappeto guidatore',                     2, 'post3', []),
    (5,  'tappeto_passeggero',           'Tappeto passeggero',                    2, 'post3', []),
    (0,  'parabrezza_interno',           'Parabrezza interno',                    3, 'post4', []),
    (1,  'lunotto_interno',              'Lunotto interno',                       3, 'post4', []),
    (2,  'vetri_laterali_interni',       'Vetri laterali interni',                3, 'post4', []),
    (3,  'specchietto_retrovisore_int',  'Specchietto retrovisore interno',       3, 'post4', []),
    (4,  'parasoli',                     'Parasoli',                              3, 'post4', []),
    (0,  'cofano_interno',               'Cofano interno',                        4, 'post3', []),
    (1,  'bagagliaio',                   'Bagagliaio',                            4, 'post3', []),
    (2,  'cassetto_guanti',              'Cassetto guanti',                       4, 'post4', []),
]

CATEGORIE_DIFETTO = [
    (0, 'Sporco residuo'),
    (1, 'Trattamento non corretto'),
    (2, 'Mancanza'),
    (3, 'Danno'),
]

TIPI_DIFETTO = [
    # (ordine, codice, nome, cat_ordine, richiede_desc)
    (0, 'briciole_sabbia_polvere',   'Briciole / sabbia / polvere',     0, False),
    (1, 'fango',                     'Fango',                           0, False),
    (2, 'macchia_organica',          'Macchia organica',                0, False),
    (3, 'escrementi_insetti',        'Escrementi / insetti',            0, False),
    (0, 'alone_vetro',               'Alone su vetro',                  1, False),
    (1, 'alone_plastica',            'Alone su plastica',               1, False),
    (2, 'prodotto_non_rimosso',      'Prodotto non rimosso',            1, False),
    (3, 'nero_gomme_non_uniforme',   'Nero gomme non uniforme',         1, False),
    (0, 'zona_non_trattata',         'Zona non trattata',               2, False),
    (1, 'profumo_non_applicato',     'Profumo non applicato',           2, False),
    (2, 'foglio_protettivo_mancante','Foglio protettivo mancante',      2, False),
    (3, 'nero_gomme_mancante',       'Nero gomme mancante',             2, False),
    (0, 'graffio_carrozzeria',       'Graffio carrozzeria',             3, False),
    (1, 'graffio_plastica',          'Graffio plastica interna',        3, False),
    (2, 'altro',                     'Altro',                           3, True),
]

MAPPINGS = {
    'carrozzeria_aloni_residui': ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'parabrezza_esterno':        ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','alone_vetro','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'lunotto_esterno':           ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','alone_vetro','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'vetri_laterali_esterni':    ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','alone_vetro','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'specchietti_esterni':       ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','alone_vetro','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'cerchi':                    ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','graffio_carrozzeria','altro'],
    'passaruota':                ['briciole_sabbia_polvere','fango','zona_non_trattata','altro'],
    'gomme_nero_gomme':          ['nero_gomme_non_uniforme','nero_gomme_mancante','zona_non_trattata','altro'],
    'paraurti':                  ['briciole_sabbia_polvere','fango','macchia_organica','escrementi_insetti','prodotto_non_rimosso','zona_non_trattata','graffio_carrozzeria','altro'],
    'cruscotto_plancia':         ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'bocchette_aria':            ['briciole_sabbia_polvere','alone_plastica','prodotto_non_rimosso','zona_non_trattata','altro'],
    'tunnel_centrale':           ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'montanti':                  ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'battitacchi':               ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'sedile_guidatore':          ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','foglio_protettivo_mancante','profumo_non_applicato','graffio_plastica','altro'],
    'sedile_passeggero':         ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','foglio_protettivo_mancante','graffio_plastica','altro'],
    'sedili_posteriori':         ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','foglio_protettivo_mancante','graffio_plastica','altro'],
    'vani_portiere':             ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'pannelli_portiere':         ['briciole_sabbia_polvere','fango','macchia_organica','alone_plastica','prodotto_non_rimosso','zona_non_trattata','graffio_plastica','altro'],
    'moquette_ant_sx':           ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'moquette_ant_dx':           ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'moquette_post_sx':          ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'moquette_post_dx':          ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'tappeto_guidatore':         ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'tappeto_passeggero':        ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'parabrezza_interno':        ['briciole_sabbia_polvere','alone_vetro','prodotto_non_rimosso','zona_non_trattata','altro'],
    'lunotto_interno':           ['briciole_sabbia_polvere','alone_vetro','prodotto_non_rimosso','zona_non_trattata','altro'],
    'vetri_laterali_interni':    ['briciole_sabbia_polvere','alone_vetro','prodotto_non_rimosso','zona_non_trattata','altro'],
    'specchietto_retrovisore_int':['briciole_sabbia_polvere','alone_vetro','prodotto_non_rimosso','zona_non_trattata','altro'],
    'parasoli':                  ['briciole_sabbia_polvere','alone_vetro','prodotto_non_rimosso','zona_non_trattata','altro'],
    'cofano_interno':            ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'bagagliaio':                ['briciole_sabbia_polvere','fango','macchia_organica','zona_non_trattata','altro'],
    'cassetto_guanti':           ['briciole_sabbia_polvere','macchia_organica','zona_non_trattata','altro'],
}


def popola(apps, schema_editor):
    CategoriaZona = apps.get_model('cq', 'CategoriaZona')
    ZonaConfig = apps.get_model('cq', 'ZonaConfig')
    CategoriaDifetto = apps.get_model('cq', 'CategoriaDifetto')
    TipoDifettoConfig = apps.get_model('cq', 'TipoDifettoConfig')
    ZonaDifettoMapping = apps.get_model('cq', 'ZonaDifettoMapping')

    # Categorie zona
    cat_zona = {}
    for ordine, nome in CATEGORIE_ZONA:
        obj, _ = CategoriaZona.objects.get_or_create(nome=nome, defaults={'ordine': ordine})
        cat_zona[ordine] = obj

    # Zone
    zone_map = {}
    for ord_z, codice, nome, cat_ord, produttore, catena in ZONE:
        obj, _ = ZonaConfig.objects.get_or_create(
            codice=codice,
            defaults={
                'nome': nome,
                'categoria': cat_zona[cat_ord],
                'postazione_produttore': produttore,
                'postazioni_catena': catena,
                'ordine': ord_z,
            },
        )
        zone_map[codice] = obj

    # Categorie difetto
    cat_diff = {}
    for ordine, nome in CATEGORIE_DIFETTO:
        obj, _ = CategoriaDifetto.objects.get_or_create(nome=nome, defaults={'ordine': ordine})
        cat_diff[ordine] = obj

    # Tipi difetto
    tipi_map = {}
    for ord_t, codice, nome, cat_ord, req_desc in TIPI_DIFETTO:
        obj, _ = TipoDifettoConfig.objects.get_or_create(
            codice=codice,
            defaults={
                'nome': nome,
                'categoria': cat_diff[cat_ord],
                'richiede_descrizione': req_desc,
                'ordine': ord_t,
            },
        )
        tipi_map[codice] = obj

    # Mappings zona ↔ difetto
    for zona_codice, tipi_codici in MAPPINGS.items():
        zona_obj = zone_map.get(zona_codice)
        if not zona_obj:
            continue
        for tipo_codice in tipi_codici:
            tipo_obj = tipi_map.get(tipo_codice)
            if tipo_obj:
                ZonaDifettoMapping.objects.get_or_create(zona=zona_obj, tipo_difetto=tipo_obj)


def svuota(apps, schema_editor):
    apps.get_model('cq', 'ZonaDifettoMapping').objects.all().delete()
    apps.get_model('cq', 'ZonaConfig').objects.all().delete()
    apps.get_model('cq', 'CategoriaZona').objects.all().delete()
    apps.get_model('cq', 'TipoDifettoConfig').objects.all().delete()
    apps.get_model('cq', 'CategoriaDifetto').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0003_config_models'),
    ]

    operations = [
        migrations.RunPython(popola, svuota),
    ]
