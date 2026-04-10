import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cq', '0002_crea_gruppi_cq'),
    ]

    operations = [
        # ----- Nuovi modelli di configurazione -----
        migrations.CreateModel(
            name='CategoriaZona',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100, unique=True, verbose_name='Nome')),
                ('ordine', models.PositiveIntegerField(default=0, verbose_name='Ordine')),
            ],
            options={
                'verbose_name': 'Categoria zona',
                'verbose_name_plural': 'Categorie zona',
                'ordering': ['ordine', 'nome'],
            },
        ),
        migrations.CreateModel(
            name='CategoriaDifetto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100, unique=True, verbose_name='Nome')),
                ('ordine', models.PositiveIntegerField(default=0, verbose_name='Ordine')),
            ],
            options={
                'verbose_name': 'Categoria difetto',
                'verbose_name_plural': 'Categorie difetto',
                'ordering': ['ordine', 'nome'],
            },
        ),
        migrations.CreateModel(
            name='ZonaConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100, verbose_name='Nome')),
                ('codice', models.SlugField(max_length=80, unique=True, verbose_name='Codice')),
                ('postazione_produttore', models.CharField(
                    blank=True,
                    choices=[
                        ('post1', 'Postazione 1 — Pre-lavaggio'),
                        ('post2', 'Postazione 2 — Spazzole'),
                        ('post3', 'Postazione 3 — Aspirazione'),
                        ('post4', 'Postazione 4 — Plastiche e vetri'),
                        ('controllo_finale', 'Controllo finale'),
                    ],
                    max_length=20,
                    verbose_name='Postazione produttore',
                )),
                ('postazioni_catena', models.JSONField(blank=True, default=list, verbose_name='Postazioni catena')),
                ('attiva', models.BooleanField(default=True, verbose_name='Attiva')),
                ('ordine', models.PositiveIntegerField(default=0, verbose_name='Ordine')),
                ('categoria', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='zone',
                    to='cq.categoriazona',
                    verbose_name='Categoria',
                )),
            ],
            options={
                'verbose_name': 'Zona auto',
                'verbose_name_plural': 'Zone auto',
                'ordering': ['categoria__ordine', 'ordine', 'nome'],
            },
        ),
        migrations.CreateModel(
            name='TipoDifettoConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=100, verbose_name='Nome')),
                ('codice', models.SlugField(max_length=80, unique=True, verbose_name='Codice')),
                ('richiede_descrizione', models.BooleanField(default=False, verbose_name='Richiede descrizione')),
                ('attivo', models.BooleanField(default=True, verbose_name='Attivo')),
                ('ordine', models.PositiveIntegerField(default=0, verbose_name='Ordine')),
                ('categoria', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tipi',
                    to='cq.categoriadifetto',
                    verbose_name='Categoria',
                )),
            ],
            options={
                'verbose_name': 'Tipo difetto',
                'verbose_name_plural': 'Tipi difetto',
                'ordering': ['categoria__ordine', 'ordine', 'nome'],
            },
        ),
        migrations.CreateModel(
            name='ZonaDifettoMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zona', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='difetti_config',
                    to='cq.zonaconfig',
                    verbose_name='Zona',
                )),
                ('tipo_difetto', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='zone_config',
                    to='cq.tipodifettoconfig',
                    verbose_name='Tipo difetto',
                )),
            ],
            options={
                'verbose_name': 'Mapping zona-difetto',
                'verbose_name_plural': 'Mapping zona-difetto',
            },
        ),
        migrations.AlterUniqueTogether(
            name='zonadifettomapping',
            unique_together={('zona', 'tipo_difetto')},
        ),
        # ----- Modifica DifettoCQ: rimuovi choices, aumenta max_length -----
        migrations.AlterField(
            model_name='difettocq',
            name='zona',
            field=models.CharField(max_length=80, verbose_name='Zona auto'),
        ),
        migrations.AlterField(
            model_name='difettocq',
            name='tipo_difetto',
            field=models.CharField(max_length=80, verbose_name='Tipo di difetto'),
        ),
    ]
