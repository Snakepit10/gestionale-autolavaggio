"""Magazzino: quantita' e movimenti, collegamento con la cassa, ordini ai
fornitori e consegne, assegnazioni con segnalazioni e checklist di turno."""
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.auth_system.testutils import utente_titolare
from apps.core.models import Categoria, ServizioProdotto
from apps.cq.models import PostazioneCQ

from . import services
from .models import Articolo, Assegnazione, Fornitore, Movimento, OrdineFornitore


def _pagina(user, nome, **kw):
    """GET di una pagina con RequestFactory (il test client non riesce a
    copiare il contesto dei template con Python 3.14), passando dal
    controllo di sezione del middleware."""
    from django.urls import resolve

    from apps.auth_system.middleware import AuthenticationMiddleware

    url = reverse(f'magazzino:{nome}', kwargs=kw) if not nome.startswith('/') else nome
    req = RequestFactory().get(url)
    req.user = User.objects.get(pk=user.pk)
    req.session = {}
    negato = AuthenticationMiddleware(lambda r: None)._controlla_sezione(req)
    if negato is not None:
        return negato
    match = resolve(url)
    return match.func(req, *match.args, **match.kwargs)


def _post(client, nome, dati=None, **kw):
    return client.post(reverse(f'magazzino:{nome}', kwargs=kw), data=json.dumps(dati or {}),
                       content_type='application/json')


class QuantitaTest(TestCase):
    def setUp(self):
        cat = Categoria.objects.create(nome='Shop')
        self.profumo = ServizioProdotto.objects.create(
            titolo='Profumatore', categoria=cat, prezzo=Decimal('5'), descrizione='',
            tipo='prodotto', quantita_disponibile=10, quantita_minima_alert=3)
        self.panno = Articolo.objects.create(nome='Panno microfibra', tipo='consumabile', quantita=20,
                                             scorta_minima=5, costo=Decimal('1.50'))

    def test_prodotto_del_catalogo_ha_il_suo_articolo(self):
        a = self.profumo.articolo
        self.assertEqual((a.tipo, a.quantita, a.scorta_minima, a.traccia_scorte), ('vendita', 10, 3, True))
        illimitato = ServizioProdotto.objects.create(
            titolo='Arbre', categoria=self.profumo.categoria, prezzo=Decimal('3'), descrizione='',
            tipo='prodotto')
        self.assertFalse(illimitato.articolo.traccia_scorte)
        servizio = ServizioProdotto.objects.create(
            titolo='Lavaggio', categoria=self.profumo.categoria, prezzo=Decimal('10'), descrizione='',
            tipo='servizio')
        self.assertFalse(Articolo.objects.filter(prodotto=servizio).exists())

    def test_movimenta_aggiorna_e_copia_nel_prodotto(self):
        a = self.profumo.articolo
        mov = services.movimenta(a, 5, 'carico')
        self.assertEqual((mov.quantita_prima, mov.quantita_dopo), (10, 15))
        self.profumo.refresh_from_db()
        self.assertEqual(self.profumo.quantita_disponibile, 15)
        services.rettifica(a, 2)
        self.profumo.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual((a.quantita, self.profumo.quantita_disponibile), (2, 2))
        self.assertTrue(a.sotto_scorta)
        self.assertEqual(Movimento.objects.filter(articolo=a).count(), 2)

    def test_vendita_in_cassa_scarica_il_magazzino(self):
        from apps.ordini.models import ItemOrdine, Ordine
        ordine = Ordine.objects.create(totale=Decimal('10'), totale_finale=Decimal('10'))
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=self.profumo, quantita=2,
                                  prezzo_unitario=Decimal('5'))
        a = Articolo.objects.get(prodotto=self.profumo)
        self.assertEqual(a.quantita, 8)
        mov = a.movimenti.get()
        self.assertEqual((mov.tipo, mov.quantita, mov.ordine_id), ('vendita', -2, ordine.pk))

    def test_articolo_non_tracciato_registra_senza_cambiare(self):
        a = Articolo.objects.create(nome='Acqua', traccia_scorte=False)
        services.movimenta(a, -3, 'scarto')
        a.refresh_from_db()
        self.assertEqual(a.quantita, 0)
        self.assertEqual(a.movimenti.count(), 1)

    def test_pagine_e_api_articolo(self):
        user = utente_titolare('tit', password='x')
        self.client.force_login(user)
        r = _post(self.client, 'articolo-salva', {'nome': 'Aspiratore', 'tipo': 'strumento',
                                                   'quantita_iniziale': 3, 'costo': '120,50'})
        a = Articolo.objects.get(pk=r.json()['id'])
        self.assertEqual((a.quantita, a.costo), (3, Decimal('120.50')))
        r = _post(self.client, 'articolo-movimento', {'tipo': 'scarto', 'quantita': 1}, pk=a.pk)
        self.assertEqual(r.json()['quantita'], 2)
        r = _post(self.client, 'articolo-movimento', {'tipo': 'rettifica', 'quantita': 5}, pk=a.pk)
        self.assertEqual(r.json()['quantita'], 5)
        for nome, kw in (('articoli', {}), ('articolo', {'pk': a.pk}), ('movimenti', {}),
                         ('fornitori', {}), ('ordini', {}), ('ordine-nuovo', {}), ('consegne', {}),
                         ('assegnazioni', {}), ('segnalazioni', {}), ('report', {})):
            self.assertEqual(_pagina(user, nome, **kw).status_code, 200, nome)
        # la vecchia Gestione Scorte rimanda al magazzino
        r = _pagina(user, '/scorte/')
        self.assertEqual((r.status_code, r.url), (302, reverse('magazzino:articoli')))


class OrdiniConsegneTest(TestCase):
    def setUp(self):
        self.user = utente_titolare('tit', password='x')
        self.client.force_login(self.user)
        self.fornitore = Fornitore.objects.create(ragione_sociale='Chimica Srl')
        self.shampoo = Articolo.objects.create(nome='Shampoo', quantita=1, scorta_minima=4,
                                               fornitore=self.fornitore, costo=Decimal('8'))
        self.panni = Articolo.objects.create(nome='Panni', quantita=0, scorta_minima=10)

    def test_numerazione(self):
        o1 = OrdineFornitore.objects.create(fornitore=self.fornitore)
        o2 = OrdineFornitore.objects.create(fornitore=self.fornitore)
        anno = o1.data.year
        self.assertEqual((o1.numero, o2.numero), (f'OF-{anno}-001', f'OF-{anno}-002'))

    def test_proponi_sotto_scorta(self):
        r = self.client.get(reverse('magazzino:ordine-proponi'), {'fornitore': self.fornitore.pk}).json()
        righe = {x['articolo']: x['quantita'] for x in r['righe']}
        # shampoo: 2*4 - 1 = 7; panni (nessun fornitore): 2*10 - 0 = 20
        self.assertEqual(righe, {self.shampoo.pk: 7, self.panni.pk: 20})

    def test_ordine_consegna_parziale_e_completa(self):
        r = _post(self.client, 'ordine-salva', {'fornitore': self.fornitore.pk, 'righe': [
            {'articolo': self.shampoo.pk, 'quantita': 6, 'prezzo': '7.5'},
            {'articolo': self.panni.pk, 'quantita': 20, 'prezzo': '1'}]})
        ordine = OrdineFornitore.objects.get(pk=r.json()['id'])
        self.assertEqual((ordine.stato, ordine.totale), ('bozza', Decimal('65.00')))
        self.assertEqual(_pagina(self.user, 'ordine', pk=ordine.pk).status_code, 200)
        # una bozza non riceve consegne
        self.assertEqual(_post(self.client, 'consegna-registra', {'ordine': ordine.pk, 'righe': []}).status_code, 400)
        _post(self.client, 'ordine-stato', {'stato': 'inviato'}, pk=ordine.pk)

        residuo = self.client.get(reverse('magazzino:ordine-residuo', args=[ordine.pk])).json()['righe']
        riga_shampoo = next(x for x in residuo if x['articolo'] == self.shampoo.pk)
        r = _post(self.client, 'consegna-registra', {'ordine': ordine.pk, 'numero_ddt': '123', 'righe': [
            {'riga_ordine': riga_shampoo['riga_ordine'], 'quantita': 6, 'prezzo': '7.5'}]})
        self.assertEqual(r.json()['stato_ordine'], 'parziale')
        self.shampoo.refresh_from_db()
        self.assertEqual((self.shampoo.quantita, self.shampoo.costo), (7, Decimal('7.50')))
        # l'ordine con consegne non si modifica piu'
        r = _post(self.client, 'ordine-salva', {'id': ordine.pk, 'fornitore': self.fornitore.pk,
                                                'righe': [{'articolo': self.panni.pk, 'quantita': 1}]})
        self.assertEqual(r.status_code, 400)

        residuo = self.client.get(reverse('magazzino:ordine-residuo', args=[ordine.pk])).json()['righe']
        self.assertEqual([x['quantita'] for x in residuo], [20])
        r = _post(self.client, 'consegna-registra', {'ordine': ordine.pk, 'righe': [
            {'riga_ordine': residuo[0]['riga_ordine'], 'quantita': 20, 'prezzo': '1'}]})
        self.assertEqual(r.json()['stato_ordine'], 'evaso')
        for nome in ('ordine', 'ordine-stampa'):
            self.assertEqual(_pagina(self.user, nome, pk=ordine.pk).status_code, 200, nome)

        # annullare la consegna toglie la merce e riapre l'ordine
        _post(self.client, 'consegna-elimina', pk=r.json()['id'])
        self.panni.refresh_from_db()
        ordine.refresh_from_db()
        self.assertEqual((self.panni.quantita, ordine.stato), (0, 'parziale'))

    def test_consegna_senza_ordine(self):
        r = _post(self.client, 'consegna-registra', {'fornitore': self.fornitore.pk, 'righe': [
            {'articolo': self.panni.pk, 'quantita': 50, 'prezzo': '0.8'}]})
        self.assertTrue(r.json()['success'])
        self.panni.refresh_from_db()
        self.assertEqual(self.panni.quantita, 50)


class AssegnazioniTest(TestCase):
    def setUp(self):
        self.titolare = utente_titolare('tit', password='x')
        self.operatore = User.objects.create_user('mario', password='x')
        self.operatore.groups.add(Group.objects.get(name='operatore'))
        self.altro = User.objects.create_user('luigi', password='x')
        self.altro.groups.add(Group.objects.get(name='operatore'))
        self.post = PostazioneCQ.objects.create(codice='aspirazione-t', nome='Aspirazione T')
        self.aspiratore = Articolo.objects.create(nome='Aspiratore', tipo='strumento', quantita=3,
                                                  costo=Decimal('150'))
        self.panni = Articolo.objects.create(nome='Panno', quantita=30, costo=Decimal('1'))

    def _in_turno(self, user):
        from apps.turni.models import PostazioneTurno, SessioneTurno
        sessione = SessioneTurno.objects.create(operatore=user)
        PostazioneTurno.objects.create(sessione=sessione, postazione_cq=self.post)

    def test_assegna_restituisci_e_divisione(self):
        a = services.assegna(self.panni, 10, utente=self.operatore, da=self.titolare)
        self.panni.refresh_from_db()
        self.assertEqual((self.panni.quantita, self.panni.assegnati), (20, 10))
        # 4 esauriti: l'assegnazione si divide
        esauriti = services.cambia_stato(a, 'esaurito', quantita=4, utente=self.operatore, origine='operatore')
        a.refresh_from_db()
        self.assertEqual((a.quantita, a.stato, esauriti.quantita, esauriti.stato), (6, 'in_uso', 4, 'esaurito'))
        self.assertTrue(esauriti.da_gestire)
        # restituzione: torna in magazzino
        services.cambia_stato(a, 'restituito', utente=self.titolare)
        self.panni.refresh_from_db()
        self.assertEqual((self.panni.quantita, self.panni.assegnati), (26, 0))

    def test_assegnazione_a_postazione_crea_la_voce_di_checklist(self):
        from apps.turni.models import ChecklistItem
        a = services.assegna(self.aspiratore, 1, postazione=self.post, da=self.titolare, in_chiusura=False)
        voce = ChecklistItem.objects.get(assegnazione=a)
        self.assertEqual((voce.postazione_cq, voce.nome, voce.in_apertura, voce.in_chiusura, voce.attivo),
                         (self.post, 'Aspiratore ×1', True, False, True))
        self.assertEqual(voce.categoria.nome, services.CATEGORIA_DOTAZIONE)
        self.assertEqual(set(voce.categoria.esiti.values_list('codice', flat=True)),
                         {'ok', 'usurato', 'rotto', 'mancante'})
        services.cambia_stato(a, 'restituito', utente=self.titolare)
        voce.refresh_from_db()
        self.assertFalse(voce.attivo)

    def test_esito_rotto_in_checklist_cambia_lo_stato(self):
        from apps.turni.models import ChecklistItem, SessioneTurno
        from apps.turni.views import checklist_view
        a = services.assegna(self.aspiratore, 1, postazione=self.post, da=self.titolare)
        voce = ChecklistItem.objects.get(assegnazione=a)
        rotto = voce.categoria.esiti.get(codice='rotto')
        self._in_turno(self.operatore)

        req = RequestFactory().post('/turni/checklist/', {f'esito_{voce.pk}': rotto.pk,
                                                          f'note_{voce.pk}': 'cavo tagliato'})
        req.user = self.operatore
        req.session = {}
        from django.contrib.messages.storage.fallback import FallbackStorage
        req._messages = FallbackStorage(req)
        checklist_view(req)

        a.refresh_from_db()
        self.assertEqual((a.stato, a.da_gestire), ('rotto', True))
        stato = a.storico.first()
        self.assertEqual((stato.origine, stato.nota, stato.utente), ('checklist', 'cavo tagliato', self.operatore))
        voce.refresh_from_db()
        self.assertFalse(voce.attivo)
        self.assertTrue(SessioneTurno.objects.get().checklist_inizio_compilata)

        # il responsabile sostituisce: nuovo pezzo e nuova voce di checklist
        nuova = services.sostituisci(a, da=self.titolare)
        a.refresh_from_db()
        self.assertFalse(a.da_gestire)
        self.assertEqual(nuova.origine, a)
        self.assertTrue(ChecklistItem.objects.get(assegnazione=nuova).attivo)
        self.aspiratore.refresh_from_db()
        self.assertEqual(self.aspiratore.quantita, 1)

    def test_operatore_segnala_solo_il_suo(self):
        mia = services.assegna(self.panni, 5, utente=self.operatore, da=self.titolare)
        sua = services.assegna(self.panni, 5, utente=self.altro, da=self.titolare)
        di_postazione = services.assegna(self.aspiratore, 1, postazione=self.post, da=self.titolare)
        self.client.force_login(self.operatore)

        r = _post(self.client, 'mia-dotazione-stato', {'stato': 'esaurito', 'quantita': 2}, pk=mia.pk)
        self.assertTrue(r.json()['success'])
        self.assertEqual(_post(self.client, 'mia-dotazione-stato', {'stato': 'rotto'}, pk=sua.pk).status_code, 403)
        # postazione: solo se e' nel suo turno
        self.assertEqual(_post(self.client, 'mia-dotazione-stato', {'stato': 'rotto'},
                               pk=di_postazione.pk).status_code, 403)
        self._in_turno(self.operatore)
        self.assertTrue(_post(self.client, 'mia-dotazione-stato', {'stato': 'usurato'},
                              pk=di_postazione.pk).json()['success'])
        # l'operatore non puo' "restituire" da solo
        self.assertEqual(_post(self.client, 'mia-dotazione-stato', {'stato': 'restituito'},
                               pk=mia.pk).status_code, 400)

        pagina = _pagina(self.operatore, 'mia-dotazione')
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'Aspiratore')
        self.assertNotContains(pagina, 'luigi')

    def test_permessi_sezione(self):
        from apps.auth_system.sezioni import ha_accesso, sezione_per_percorso
        self.assertEqual(sezione_per_percorso('/magazzino/'), 'magazzino')
        self.assertEqual(sezione_per_percorso('/magazzino/mia-dotazione/3/stato/'), 'mio_turno')
        self.assertEqual(sezione_per_percorso('/scorte/'), 'magazzino')
        self.assertFalse(ha_accesso(self.operatore, 'magazzino'))
        self.assertTrue(ha_accesso(self.titolare, 'magazzino'))
        self.assertEqual(_pagina(self.operatore, 'articoli').status_code, 403)
        self.assertEqual(_pagina(self.operatore, 'mia-dotazione').status_code, 200)

    def test_report(self):
        services.assegna(self.panni, 10, utente=self.operatore, da=self.titolare)
        a = services.assegna(self.aspiratore, 1, postazione=self.post, da=self.titolare)
        services.cambia_stato(a, 'rotto', utente=self.operatore, origine='operatore')
        from django.utils import timezone

        from .views import dati_report
        oggi = timezone.localdate()
        ctx = dati_report(oggi, oggi)
        self.assertEqual(_pagina(self.titolare, 'report').status_code, 200)
        self.assertEqual(ctx['totale_consumi'], Decimal('160'))
        self.assertEqual(ctx['costo_consumabili'], Decimal('10.00'))
        self.assertEqual([(r['articolo'].nome, r['codice']) for r in ctx['problemi']], [('Aspiratore', 'rotto')])


class MigrazioneScorteTest(TestCase):
    def test_prodotti_e_storico_passano_al_magazzino(self):
        import importlib

        from django.apps import apps as global_apps

        from apps.core.models import MovimentoScorte
        migrazione = importlib.import_module('apps.magazzino.migrations.0002_articoli_dai_prodotti')
        cat = Categoria.objects.create(nome='Shop')
        tracciato = ServizioProdotto.objects.create(
            titolo='Profumatore', categoria=cat, prezzo=Decimal('5'), descrizione='', tipo='prodotto',
            quantita_disponibile=7, quantita_minima_alert=2, codice_prodotto='PRF', gruppo='Profumi')
        libero = ServizioProdotto.objects.create(
            titolo='Arbre', categoria=cat, prezzo=Decimal('3'), descrizione='', tipo='prodotto')
        MovimentoScorte.objects.create(prodotto=tracciato, tipo='carico', quantita=10,
                                       quantita_prima=0, quantita_dopo=10)
        MovimentoScorte.objects.create(prodotto=tracciato, tipo='scarico', quantita=-3,
                                       quantita_prima=10, quantita_dopo=7)
        Articolo.objects.all().delete()          # come prima della migrazione

        migrazione.avanti(global_apps, None)

        a = Articolo.objects.get(prodotto=tracciato)
        self.assertEqual((a.nome, a.tipo, a.quantita, a.scorta_minima, a.codice, a.categoria, a.traccia_scorte),
                         ('Profumatore', 'vendita', 7, 2, 'PRF', 'Profumi', True))
        self.assertFalse(Articolo.objects.get(prodotto=libero).traccia_scorte)
        self.assertEqual(list(a.movimenti.order_by('data').values_list('tipo', 'quantita', 'quantita_dopo')),
                         [('carico', 10, 10), ('scarto', -3, 7)])


class ContenutoPrezzoTest(TestCase):
    """Bidone da 25 kg: prezzo al kg o al pezzo, costo salvato sempre al pezzo."""

    def setUp(self):
        self.user = utente_titolare('tit', password='x')
        self.client.force_login(self.user)

    def test_prezzo_al_kg(self):
        r = _post(self.client, 'articolo-salva', {
            'nome': 'Shampoo', 'tipo': 'consumabile', 'unita': 'bidoni', 'contenuto': '25',
            'unita_contenuto': 'kg', 'prezzo_per': 'contenuto', 'costo': '1,37', 'quantita_iniziale': 4})
        a = Articolo.objects.get(pk=r.json()['id'])
        self.assertEqual((a.costo, a.costo_indicato, a.unita_prezzo), (Decimal('34.25'), Decimal('1.37'), 'kg'))
        self.assertEqual((a.quantita_contenuto, a.valore), (Decimal('100'), Decimal('137.00')))

        # ordine con prezzo al kg: importo = 2 bidoni x 25 kg x 1,40
        fornitore = Fornitore.objects.create(ragione_sociale='Chimica')
        r = _post(self.client, 'ordine-salva', {'fornitore': fornitore.pk, 'righe': [
            {'articolo': a.pk, 'quantita': 2, 'prezzo': '1.40'}]})
        ordine = OrdineFornitore.objects.get(pk=r.json()['id'])
        riga = ordine.righe.get()
        self.assertEqual((riga.prezzo, riga.prezzo_indicato, riga.importo, riga.quantita_contenuto),
                         (Decimal('35.0000'), Decimal('1.4000'), Decimal('70.00'), Decimal('50')))
        self.assertEqual(_pagina(self.user, 'ordine-stampa', pk=ordine.pk).status_code, 200)

        # consegna: prezzo indicato al kg, aggiorna il costo al pezzo
        _post(self.client, 'ordine-stato', {'stato': 'inviato'}, pk=ordine.pk)
        residuo = self.client.get(reverse('magazzino:ordine-residuo', args=[ordine.pk])).json()['righe']
        self.assertEqual((residuo[0]['prezzo'], residuo[0]['unita_prezzo'], residuo[0]['pezzo']), ('1.4', 'kg', '25 kg'))
        _post(self.client, 'consegna-registra', {'ordine': ordine.pk, 'righe': [
            {'riga_ordine': residuo[0]['riga_ordine'], 'quantita': 2, 'prezzo': '1.5'}]})
        a.refresh_from_db()
        self.assertEqual((a.quantita, a.costo), (6, Decimal('37.5000')))

    def test_prezzo_al_pezzo_mostra_anche_al_litro(self):
        a = Articolo.objects.create(nome='Lucidante', unita='flaconi', contenuto=Decimal('0.75'),
                                    unita_contenuto='l', costo=Decimal('6'))
        self.assertEqual((a.costo_indicato, a.unita_prezzo, a.costo_al_contenuto), (Decimal('6'), 'flaconi', Decimal('8')))

    def test_validazioni(self):
        r = _post(self.client, 'articolo-salva', {'nome': 'X', 'contenuto': '25'})
        self.assertEqual(r.status_code, 400)
        r = _post(self.client, 'articolo-salva', {'nome': 'X', 'prezzo_per': 'contenuto'})
        self.assertEqual(r.status_code, 400)
