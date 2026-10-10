"""Sottocategorie del catalogo (campo `gruppo` di ServizioProdotto)."""
import json
from decimal import Decimal

from django.contrib.auth.models import User
from apps.auth_system.testutils import utente_titolare
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.core.models import Categoria, ServizioProdotto


class SottocategorieTest(TestCase):
    def setUp(self):
        self.prodotti = Categoria.objects.create(nome='Prodotti')
        self.altra = Categoria.objects.create(nome='Servito')
        crea = lambda titolo, gruppo, cat=self.prodotti: ServizioProdotto.objects.create(
            titolo=titolo, categoria=cat, prezzo=Decimal('5'), descrizione='',
            tipo='prodotto', gruppo=gruppo)
        crea('Arbre magique', 'Profumi')
        crea('Profumo auto', 'Profumi')
        crea('Panno microfibra', 'Panni')
        crea('Spugna', '')
        crea('Profumo in altra categoria', 'Profumi', self.altra)
        self.user = utente_titolare('op', 'op@x.it', 'x', is_staff=True)

    def test_elenco_sottocategorie_per_categoria(self):
        from apps.core.views import CategoriaListView
        req = RequestFactory().get('/')
        req.user = self.user
        vista = CategoriaListView(); vista.setup(req); vista.object_list = vista.get_queryset()
        ctx = vista.get_context_data()
        per_nome = {c.nome: c.sottocategorie for c in ctx['categorie']}
        self.assertEqual(sorted(per_nome['Prodotti']), [('Panni', 1), ('Profumi', 2)])
        self.assertEqual(per_nome['Servito'], [('Profumi', 1)])

    def _post(self, categoria, dati):
        self.client.force_login(self.user)
        return self.client.post(reverse('core:categoria-sottocategoria', args=[categoria.pk]),
                                data=json.dumps(dati), content_type='application/json').json()

    def test_rinomina_solo_nella_categoria(self):
        r = self._post(self.prodotti, {'vecchio': 'Profumi', 'nuovo': 'Profumatori'})
        self.assertEqual((r['success'], r['aggiornati']), (True, 2))
        self.assertEqual(ServizioProdotto.objects.filter(gruppo='Profumatori').count(), 2)
        # la sottocategoria omonima di un'altra categoria non cambia
        self.assertTrue(ServizioProdotto.objects.filter(categoria=self.altra, gruppo='Profumi').exists())

    def test_togli_sottocategoria(self):
        self._post(self.prodotti, {'vecchio': 'Panni', 'nuovo': ''})
        self.assertEqual(ServizioProdotto.objects.get(titolo='Panno microfibra').gruppo, '')

    def test_form_suggerisce_sottocategorie(self):
        from apps.core.forms import ServizioProdottoForm
        form = ServizioProdottoForm()
        self.assertEqual(form.fields['gruppo'].label, 'Sottocategoria')
        self.assertEqual(form.sottocategorie_esistenti, ['Panni', 'Profumi'])
