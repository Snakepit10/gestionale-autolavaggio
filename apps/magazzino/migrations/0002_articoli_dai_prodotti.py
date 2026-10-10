"""Le scorte dei prodotti del catalogo passano al magazzino.

- ogni prodotto (tipo 'prodotto') diventa un articolo 'in vendita' con la
  stessa quantita' (-1 = non tracciato), soglia e codice;
- lo storico della vecchia Gestione Scorte (core.MovimentoScorte) viene
  copiato nei movimenti di magazzino;
- la sezione Magazzino va al gruppo titolare.
"""
from django.db import migrations


def avanti(apps, schema_editor):
    ServizioProdotto = apps.get_model('core', 'ServizioProdotto')
    MovimentoScorte = apps.get_model('core', 'MovimentoScorte')
    Articolo = apps.get_model('magazzino', 'Articolo')
    Movimento = apps.get_model('magazzino', 'Movimento')

    per_prodotto = {}
    for p in ServizioProdotto.objects.filter(tipo='prodotto'):
        q = p.quantita_disponibile
        per_prodotto[p.pk] = Articolo.objects.create(
            nome=p.titolo, tipo='vendita', codice=p.codice_prodotto or '',
            categoria=p.gruppo or '', quantita=max(q, 0), traccia_scorte=q >= 0,
            scorta_minima=p.quantita_minima_alert if q >= 0 else 0,
            attivo=p.attivo, prodotto=p)

    for m in MovimentoScorte.objects.all().order_by('data_movimento'):
        articolo = per_prodotto.get(m.prodotto_id)
        if articolo is None:
            continue
        if m.tipo == 'scarico':
            tipo = 'vendita' if m.riferimento_ordine_id else 'scarto'
        else:
            tipo = m.tipo
        mov = Movimento.objects.create(
            articolo=articolo, tipo=tipo, quantita=m.quantita, quantita_prima=m.quantita_prima,
            quantita_dopo=m.quantita_dopo, ordine_id=m.riferimento_ordine_id,
            nota=(m.nota or '')[:255], operatore_id=m.operatore_id)
        # data con default: si imposta dopo per conservare quella originale
        Movimento.objects.filter(pk=mov.pk).update(data=m.data_movimento)

    Group = apps.get_model('auth', 'Group')
    PermessoGruppo = apps.get_model('auth_system', 'PermessoGruppo')
    titolare, _ = Group.objects.get_or_create(name='titolare')
    PermessoGruppo.objects.get_or_create(sezione='magazzino', gruppo=titolare)


def indietro(apps, schema_editor):
    apps.get_model('magazzino', 'Movimento').objects.all().delete()
    apps.get_model('magazzino', 'Articolo').objects.all().delete()
    apps.get_model('auth_system', 'PermessoGruppo').objects.filter(sezione='magazzino').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('magazzino', '0001_initial'),
        ('core', '0013_programmi_portale_priorita'),
        ('auth_system', '0002_permessi_iniziali'),
    ]
    operations = [migrations.RunPython(avanti, indietro)]
