"""Permessi per sezione: regola di accesso, middleware e pagina Permessi."""
import json

from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.auth_system.models import PermessoGruppo, PermessoUtente
from apps.auth_system.sezioni import ha_accesso, sezione_per_percorso


class SezioniTest(TestCase):
    def setUp(self):
        self.operatore = User.objects.create_user('op', password='x')
        self.operatore.groups.add(Group.objects.get(name='operatore'))
        self.titolare = User.objects.create_user('tit', password='x')
        self.titolare.groups.add(Group.objects.get(name='titolare'))

    def _ricarica(self, user):
        return User.objects.get(pk=user.pk)   # niente cache delle sezioni

    def test_prefisso_piu_lungo_ed_esclusioni(self):
        self.assertEqual(sezione_per_percorso('/ordini/cassa/'), 'cassa')
        self.assertEqual(sezione_per_percorso('/ordini/prodotti/'), 'ordini')
        self.assertEqual(sezione_per_percorso('/turni/report/'), 'report_turni')
        self.assertEqual(sezione_per_percorso('/turni/dashboard/'), 'mio_turno')
        self.assertEqual(sezione_per_percorso('/cq/configurazione/'), 'configurazione')
        self.assertEqual(sezione_per_percorso('/api/whatsapp/conversazioni/'), 'messaggi')
        for libero in ('/api/whatsapp/webhook/', '/app/servizi/', '/clienti/cerca/',
                       '/ordini/api/stato-carrello/', '/abbonamenti/verifica/ABC/', '/', '/admin/'):
            self.assertIsNone(sezione_per_percorso(libero), libero)

    def test_permessi_iniziali_come_oggi(self):
        self.assertTrue(ha_accesso(self.operatore, 'cassa'))
        self.assertTrue(ha_accesso(self.operatore, 'mio_turno'))
        self.assertFalse(ha_accesso(self.operatore, 'marketing'))
        self.assertFalse(ha_accesso(self.operatore, 'finanze'))
        self.assertTrue(ha_accesso(self.titolare, 'finanze'))
        self.assertTrue(ha_accesso(self.titolare, 'permessi'))
        admin = User.objects.create_superuser('boss', password='x')
        self.assertTrue(ha_accesso(admin, 'permessi'))

    def test_eccezioni_per_utente(self):
        PermessoUtente.objects.create(user=self.operatore, sezione='marketing', consenti=True)
        PermessoUtente.objects.create(user=self.operatore, sezione='cassa', consenti=False)
        op = self._ricarica(self.operatore)
        self.assertTrue(ha_accesso(op, 'marketing'))
        self.assertFalse(ha_accesso(op, 'cassa'))
        # la pagina Permessi non si apre con un'eccezione
        PermessoUtente.objects.create(user=self.operatore, sezione='permessi', consenti=True)
        self.assertFalse(ha_accesso(self._ricarica(self.operatore), 'permessi'))

    def test_middleware_blocca_pagine_e_api(self):
        self.client.force_login(self.operatore)
        r = self.client.post('/api/whatsapp/conversazioni/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()['success'])
        from apps.auth_system.middleware import AuthenticationMiddleware
        req = RequestFactory().get('/finanze/report-giornata/')
        req.user = self.operatore
        risposta = AuthenticationMiddleware(lambda r: None)._controlla_sezione(req)
        self.assertEqual(risposta.status_code, 403)
        req = RequestFactory().get('/ordini/cassa/')
        req.user = self.operatore
        self.assertIsNone(AuthenticationMiddleware(lambda r: None)._controlla_sezione(req))

    def test_pagina_permessi_api(self):
        operatore = Group.objects.get(name='operatore')
        self.client.force_login(self.operatore)
        r = self.client.post(reverse('permessi:api-gruppo'), data=json.dumps(
            {'sezione': 'marketing', 'gruppo_id': operatore.pk, 'attivo': True}), content_type='application/json')
        self.assertEqual(r.status_code, 403)                   # l'operatore non gestisce i permessi
        self.client.force_login(self.titolare)
        r = self.client.post(reverse('permessi:api-gruppo'), data=json.dumps(
            {'sezione': 'marketing', 'gruppo_id': operatore.pk, 'attivo': True}), content_type='application/json')
        self.assertTrue(r.json()['success'])
        self.assertTrue(PermessoGruppo.objects.filter(sezione='marketing', gruppo=operatore).exists())
        r = self.client.post(reverse('permessi:api-gruppo'), data=json.dumps(
            {'sezione': 'permessi', 'gruppo_id': operatore.pk, 'attivo': True}), content_type='application/json')
        self.assertEqual(r.status_code, 400)                   # sezione fissa
        r = self.client.post(reverse('permessi:api-eccezione'), data=json.dumps(
            {'sezione': 'finanze', 'user_id': self.operatore.pk, 'consenti': True}), content_type='application/json')
        self.assertTrue(PermessoUtente.objects.get(user=self.operatore, sezione='finanze').consenti)
