"""Test archiviazione ordini non pagati (riservata all'amministratore)."""
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.ordini.models import Ordine


class ArchiviaNonPagatiTest(TestCase):
    def setUp(self):
        self.ordine = Ordine.objects.create(
            totale=Decimal('22'), totale_finale=Decimal('22'),
            stato_pagamento='non_pagato')
        self.url = reverse('ordini:archivia-non-pagati')

    def _post(self, user, archivia=True):
        self.client.force_login(user)
        return self.client.post(
            self.url, data=json.dumps({'ordini': [self.ordine.pk], 'archivia': archivia}),
            content_type='application/json')

    def test_operatore_staff_non_puo_archiviare(self):
        staff = User.objects.create_user('op', 'op@x.it', 'x', is_staff=True)
        r = self._post(staff)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()['success'])
        self.ordine.refresh_from_db()
        self.assertFalse(self.ordine.non_pagato_archiviato)

    def test_admin_archivia_e_ripristina(self):
        admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        self.assertTrue(self._post(admin).json()['success'])
        self.ordine.refresh_from_db()
        self.assertTrue(self.ordine.non_pagato_archiviato)
        self.assertTrue(self._post(admin, archivia=False).json()['success'])
        self.ordine.refresh_from_db()
        self.assertFalse(self.ordine.non_pagato_archiviato)


class VenditaProdottiTest(TestCase):
    """Vendite di soli prodotti: numerazione separata e scheda dedicata."""

    def setUp(self):
        from apps.core.models import Categoria, ServizioProdotto
        cat = Categoria.objects.create(nome='Shop')
        self.profumo = ServizioProdotto.objects.create(
            titolo='Profumatore', categoria=cat, prezzo=Decimal('5'), descrizione='',
            tipo='prodotto', quantita_disponibile=-1)
        self.lavaggio = ServizioProdotto.objects.create(
            titolo='Lavaggio esterno', categoria=cat, prezzo=Decimal('10'), descrizione='',
            tipo='servizio')
        self.user = User.objects.create_user('op', 'op@x.it', 'x', is_staff=True)

    def _ordine(self, **kw):
        return Ordine.objects.create(totale=Decimal('5'), totale_finale=Decimal('5'), **kw)

    def test_numerazione_ordini_non_bucata_dalle_vendite(self):
        o1 = self._ordine()
        v1 = self._ordine(vendita_prodotti=True)
        o2 = self._ordine()
        v2 = self._ordine(vendita_prodotti=True)
        self.assertEqual((o1.numero_breve, o2.numero_breve), (1, 2))
        self.assertTrue(v1.numero_progressivo.startswith('V'))
        self.assertEqual((v1.numero_display, v2.numero_display), ('V-001', 'V-002'))
        self.assertEqual(o2.numero_display, '#2')

    def _cassa(self, *prodotti):
        self.client.force_login(self.user)
        for sp in prodotti:
            self.client.post(reverse('ordini:aggiungi-carrello'),
                             data=json.dumps({'servizio_prodotto_id': sp.pk, 'quantita': 1}),
                             content_type='application/json')
        return self.client.post(reverse('ordini:completa-ordine'),
                                data=json.dumps({'metodo_pagamento': 'contanti', 'importo_pagamento': '5'}),
                                content_type='application/json').json()

    def test_cassa_solo_prodotti_e_una_vendita(self):
        r = self._cassa(self.profumo)
        self.assertTrue(r['success'] and r['vendita_prodotti'])
        self.assertEqual(r['numero_display'], 'V-001')
        v = Ordine.objects.get(pk=r['ordine_id'])
        self.assertTrue(v.vendita_prodotti and v.auto_ritirata)
        self.assertEqual(v.stato, 'completato')

    def test_scheda_e_lista_ordini(self):
        from datetime import timedelta
        from django.test import RequestFactory
        from django.utils import timezone

        from apps.ordini.models import ItemOrdine
        from apps.ordini.views import OrdiniListView, VenditaProdottiView
        v = self._ordine(vendita_prodotti=True)
        ItemOrdine.objects.create(ordine=v, servizio_prodotto=self.profumo, quantita=2,
                                  prezzo_unitario=Decimal('5'))
        misto = self._ordine()
        ItemOrdine.objects.create(ordine=misto, servizio_prodotto=self.profumo, quantita=1,
                                  prezzo_unitario=Decimal('5'))
        oggi = timezone.localtime(v.data_ora).date()
        req = RequestFactory().get('/', {'data': oggi.isoformat()})
        req.user = self.user

        vista = VenditaProdottiView(); vista.setup(req)
        ctx = vista.get_context_data()
        self.assertEqual([x.pk for x in ctx['vendite']], [v.pk])

        from apps.ordini.vendite import riepilogo_prodotti
        rp = riepilogo_prodotti(oggi, oggi)
        self.assertEqual((rp['n_vendite'], rp['pezzi'], rp['totale_prodotti'], rp['totale_in_ordini']),
                         (1, 3, Decimal('15.00'), Decimal('5.00')))
        self.assertEqual(rp['prodotti'][0]['in_ordini'], 1)

        lista = OrdiniListView(); lista.setup(req); lista.object_list = []
        ctx = lista.get_context_data()
        visti = {o.pk for o in list(ctx['ordini_attivi']) + list(ctx['ordini_da_ritirare'])
                 + list(ctx['ordini_completati'])}
        self.assertNotIn(v.pk, visti)
        self.assertIn(misto.pk, visti)


class VenditaProdottiCrudTest(TestCase):
    """Gestione delle vendite prodotti dalla scheda: crea, modifica
    quantita', aggiungi/togli prodotti, pagamento, elimina."""

    def setUp(self):
        from apps.core.models import Categoria, ServizioProdotto
        cat = Categoria.objects.create(nome='Shop')
        self.profumo = ServizioProdotto.objects.create(
            titolo='Profumatore', categoria=cat, prezzo=Decimal('5'), descrizione='',
            tipo='prodotto', quantita_disponibile=10)
        self.panno = ServizioProdotto.objects.create(
            titolo='Panno', categoria=cat, prezzo=Decimal('3'), descrizione='',
            tipo='prodotto', quantita_disponibile=-1)
        self.user = User.objects.create_user('op', 'op@x.it', 'x', is_staff=True)
        self.admin = User.objects.create_superuser('boss', 'b@x.it', 'x')
        self.client.force_login(self.user)

    def _post(self, nome, dati=None, **kw):
        return self.client.post(reverse(f'ordini:{nome}', kwargs=kw), data=json.dumps(dati or {}),
                                content_type='application/json')

    def _nuova(self, pagamento='contanti'):
        r = self._post('vendita-nuova', {'righe': [{'prodotto_id': self.profumo.pk, 'quantita': 2}],
                                         'pagamento': pagamento})
        self.assertTrue(r.json()['success'])
        return Ordine.objects.get(vendita_prodotti=True)

    def test_crea_vendita_pagata_scarica_magazzino(self):
        v = self._nuova()
        self.profumo.refresh_from_db()
        self.assertEqual((v.totale_finale, v.stato_pagamento, self.profumo.quantita_disponibile),
                         (Decimal('10.00'), 'pagato', 8))
        self.assertEqual(v.numero_display, 'V-001')

    def test_modifica_quantita_ricalcola_e_rettifica_magazzino(self):
        v = self._nuova(pagamento='non_pagato')
        item = v.items.get()
        self.assertTrue(self._post('vendita-modifica-item', {'quantita': 5}, pk=v.pk,
                                   item_id=item.pk).json()['success'])
        v.refresh_from_db(); self.profumo.refresh_from_db()
        self.assertEqual((v.totale_finale, self.profumo.quantita_disponibile), (Decimal('25.00'), 5))
        self.assertEqual(self._post('vendita-modifica-item', {'quantita': 0}, pk=v.pk,
                                    item_id=item.pk).status_code, 400)

    def test_aggiungi_e_togli_prodotto(self):
        v = self._nuova()
        self._post('vendita-aggiungi-item', {'prodotto_id': self.panno.pk, 'quantita': 1}, pk=v.pk)
        v.refresh_from_db()
        self.assertEqual((v.totale_finale, v.stato_pagamento), (Decimal('13.00'), 'parziale'))
        panno = v.items.get(servizio_prodotto=self.panno)
        self._post('vendita-elimina-item', pk=v.pk, item_id=panno.pk)
        v.refresh_from_db()
        self.assertEqual((v.totale_finale, v.stato_pagamento), (Decimal('10.00'), 'pagato'))
        # l'ultimo prodotto non si toglie
        self.assertEqual(self._post('vendita-elimina-item', pk=v.pk,
                                    item_id=v.items.get().pk).status_code, 400)

    def test_stato_pagamento(self):
        v = self._nuova(pagamento='non_pagato')
        self.assertEqual(v.stato_pagamento, 'non_pagato')
        r = self._post('vendita-pagamento', {'stato': 'pagato', 'metodo': 'carta'}, pk=v.pk)
        self.assertEqual(r.json()['stato_pagamento'], 'pagato')
        self.assertEqual(v.pagamenti.get().metodo, 'carta')
        r = self._post('vendita-pagamento', {'stato': 'non_pagato'}, pk=v.pk)
        self.assertEqual(r.json()['stato_pagamento'], 'non_pagato')
        self.assertFalse(v.pagamenti.exists())

    def test_elimina_solo_admin_e_rimette_in_magazzino(self):
        v = self._nuova()
        self.assertEqual(self._post('vendita-elimina', pk=v.pk).status_code, 403)
        self.client.force_login(self.admin)
        self.assertTrue(self._post('vendita-elimina', pk=v.pk).json()['success'])
        self.profumo.refresh_from_db()
        self.assertFalse(Ordine.objects.filter(pk=v.pk).exists())
        self.assertEqual(self.profumo.quantita_disponibile, 10)

    def test_non_tocca_ordini_normali(self):
        from django.http import Http404
        from django.test import RequestFactory

        from apps.ordini.views import vendita_pagamento
        ordine = Ordine.objects.create(totale=Decimal('5'), totale_finale=Decimal('5'))
        req = RequestFactory().post('/', data=json.dumps({'stato': 'non_pagato'}),
                                    content_type='application/json')
        req.user = self.user
        with self.assertRaises(Http404):
            vendita_pagamento(req, pk=ordine.pk)
