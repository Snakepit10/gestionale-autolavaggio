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
