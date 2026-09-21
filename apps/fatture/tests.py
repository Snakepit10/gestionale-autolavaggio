"""Test del flusso fatture: flag richiesta, raggruppamento, stati."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.clienti.models import Cliente
from apps.ordini.models import Ordine, Pagamento

from .models import Fattura, RigaFattura


def _crea_ordine(cliente=None, totale='10.00', **extra):
    return Ordine.objects.create(
        cliente=cliente,
        totale=Decimal(totale),
        totale_finale=Decimal(totale),
        **extra,
    )


class BaseFattureTest(TestCase):
    def setUp(self):
        self.operatore = User.objects.create_user(
            'op_fatture', 'op@test.it', 'pw', is_staff=True)
        self.client.force_login(self.operatore)
        self.cliente_a = Cliente.objects.create(
            tipo='azienda', ragione_sociale='Rossi Trasporti SRL',
            telefono='3330000001')
        self.cliente_b = Cliente.objects.create(
            tipo='privato', nome='Mario', cognome='Bianchi',
            telefono='3330000002')

    def _post_json(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type='application/json')


class RichiedeFatturaTest(BaseFattureTest):
    def test_flag_on_salva_dati(self):
        ordine = _crea_ordine(self.cliente_a)
        r = self._post_json(
            reverse('ordini:imposta-richiede-fattura', args=[ordine.pk]),
            {'richiede_fattura': True, 'targa': 'ab123cd',
             'matricola': 'M-42', 'nota': 'rif. ODA 7'})
        self.assertTrue(r.json()['success'])
        ordine.refresh_from_db()
        self.assertTrue(ordine.richiede_fattura)
        self.assertEqual(ordine.fattura_targa, 'AB123CD')
        self.assertEqual(ordine.fattura_matricola, 'M-42')
        self.assertEqual(ordine.fattura_nota, 'rif. ODA 7')

    def test_flag_off_azzera_dati(self):
        ordine = _crea_ordine(self.cliente_a, richiede_fattura=True,
                              fattura_targa='XX000XX', fattura_nota='n')
        r = self._post_json(
            reverse('ordini:imposta-richiede-fattura', args=[ordine.pk]),
            {'richiede_fattura': False})
        self.assertTrue(r.json()['success'])
        ordine.refresh_from_db()
        self.assertFalse(ordine.richiede_fattura)
        self.assertEqual(ordine.fattura_targa, '')
        self.assertEqual(ordine.fattura_nota, '')

    def test_rifiuta_se_gia_in_fattura(self):
        fattura = Fattura.objects.create(
            numero='1/2026', data=date(2026, 1, 1),
            ragione_sociale='X', cliente=self.cliente_a)
        ordine = _crea_ordine(self.cliente_a, richiede_fattura=True,
                              fattura=fattura)
        r = self._post_json(
            reverse('ordini:imposta-richiede-fattura', args=[ordine.pk]),
            {'richiede_fattura': False})
        self.assertFalse(r.json()['success'])


class CreaFatturaTest(BaseFattureTest):
    def test_crea_stesso_cliente(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        o2 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk, o2.pk], 'data': '2026-09-17',
            'numero': '1/2026', 'ragione_sociale': 'Rossi Trasporti SRL'})
        dati = r.json()
        self.assertTrue(dati['success'])
        fattura = Fattura.objects.get(pk=dati['fattura_id'])
        self.assertEqual(fattura.stato, 'da_pagare')  # ordini non pagati
        self.assertEqual(fattura.ordini.count(), 2)
        self.assertEqual(fattura.totale, Decimal('20.00'))

    def test_clienti_misti_rifiutata(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        o2 = _crea_ordine(self.cliente_b, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk, o2.pk], 'data': '2026-09-17',
            'numero': '1/2026', 'ragione_sociale': 'X'})
        self.assertFalse(r.json()['success'])

    def test_senza_cliente_con_ragione_sociale_manuale(self):
        o1 = _crea_ordine(None, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk], 'data': '2026-09-17',
            'numero': '2/2026', 'ragione_sociale': 'Ditta Manuale SNC'})
        self.assertTrue(r.json()['success'])
        o1.refresh_from_db()
        self.assertIsNone(o1.fattura.cliente)
        self.assertEqual(o1.fattura.ragione_sociale, 'Ditta Manuale SNC')

    def test_ordine_non_flaggato_rifiutato(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=False)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk], 'data': '2026-09-17',
            'numero': '1/2026', 'ragione_sociale': 'X'})
        self.assertFalse(r.json()['success'])

    def test_numero_duplicato_warning_poi_conferma(self):
        Fattura.objects.create(numero='5/2026', data=date(2026, 1, 1),
                               ragione_sociale='X')
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        payload = {'ordini': [o1.pk], 'data': '2026-09-17',
                   'numero': '5/2026', 'ragione_sociale': 'Y'}
        r = self._post_json(reverse('fatture:crea-fattura'), payload)
        self.assertTrue(r.json().get('warning_duplicato'))
        self.assertEqual(Fattura.objects.filter(numero='5/2026').count(), 1)
        payload['conferma_duplicato'] = True
        r = self._post_json(reverse('fatture:crea-fattura'), payload)
        self.assertTrue(r.json()['success'])
        self.assertEqual(Fattura.objects.filter(numero='5/2026').count(), 2)

    def test_stato_iniziale_pagata_se_tutti_saldati(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        Pagamento.objects.create(ordine=o1, importo=Decimal('10.00'),
                                 metodo='contanti')
        o1.refresh_from_db()
        self.assertTrue(o1.is_pagato)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk], 'data': '2026-09-17',
            'numero': '3/2026', 'ragione_sociale': 'X'})
        dati = r.json()
        self.assertTrue(dati['success'])
        self.assertEqual(dati['stato'], 'pagata')


class StatiFatturaTest(BaseFattureTest):
    def _fattura_da_pagare(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        o2 = _crea_ordine(self.cliente_a, richiede_fattura=True,
                          totale='15.00')
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk, o2.pk], 'data': '2026-09-17',
            'numero': '10/2026', 'ragione_sociale': 'X'})
        return Fattura.objects.get(pk=r.json()['fattura_id'])

    def test_segna_pagata_salda_gli_ordini(self):
        fattura = self._fattura_da_pagare()
        r = self._post_json(
            reverse('fatture:segna-pagata', args=[fattura.pk]),
            {'metodo': 'bonifico'})
        self.assertTrue(r.json()['success'])
        fattura.refresh_from_db()
        self.assertEqual(fattura.stato, 'pagata')
        self.assertIsNotNone(fattura.pagata_il)
        for ordine in fattura.ordini.all():
            self.assertEqual(ordine.stato_pagamento, 'pagato')
            pagamento = ordine.pagamenti.get()
            self.assertEqual(pagamento.importo, ordine.totale_finale)
            self.assertIn('10/2026', pagamento.riferimento)

    def test_archivia_solo_da_pagata(self):
        fattura = self._fattura_da_pagare()
        r = self._post_json(reverse('fatture:archivia', args=[fattura.pk]), {})
        self.assertFalse(r.json()['success'])  # ancora da_pagare
        self._post_json(reverse('fatture:segna-pagata', args=[fattura.pk]),
                        {'metodo': 'bonifico'})
        r = self._post_json(reverse('fatture:archivia', args=[fattura.pk]), {})
        self.assertTrue(r.json()['success'])
        fattura.refresh_from_db()
        self.assertEqual(fattura.stato, 'archiviata')

    def test_elimina_libera_gli_ordini_ma_non_archiviata(self):
        fattura = self._fattura_da_pagare()
        ordini_ids = list(fattura.ordini.values_list('pk', flat=True))
        r = self._post_json(reverse('fatture:elimina', args=[fattura.pk]), {})
        self.assertTrue(r.json()['success'])
        for pk in ordini_ids:
            ordine = Ordine.objects.get(pk=pk)
            self.assertIsNone(ordine.fattura_id)
            self.assertTrue(ordine.richiede_fattura)

        fattura2 = self._fattura_da_pagare()
        self._post_json(reverse('fatture:segna-pagata', args=[fattura2.pk]),
                        {'metodo': 'contanti'})
        self._post_json(reverse('fatture:archivia', args=[fattura2.pk]), {})
        r = self._post_json(reverse('fatture:elimina', args=[fattura2.pk]), {})
        self.assertFalse(r.json()['success'])


class ImportiERigheTest(BaseFattureTest):
    def test_crea_con_importo_modificato_e_righe_manuali(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)  # 10.00
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk], 'importi': {str(o1.pk): '8.50'},
            'righe': [{'data': '2026-09-10',
                       'descrizione': 'Lavaggio tappezzeria furgone',
                       'importo': '40.00'}],
            'data': '2026-09-18', 'numero': '20/2026',
            'ragione_sociale': 'X'})
        dati = r.json()
        self.assertTrue(dati['success'])
        fattura = Fattura.objects.get(pk=dati['fattura_id'])
        o1.refresh_from_db()
        self.assertEqual(o1.fattura_importo, Decimal('8.50'))
        self.assertEqual(o1.totale_finale, Decimal('10.00'))  # intatto
        self.assertEqual(fattura.totale, Decimal('48.50'))
        self.assertEqual(fattura.righe.count(), 1)

    def test_righe_manuali_forzano_da_pagare(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        Pagamento.objects.create(ordine=o1, importo=Decimal('10.00'),
                                 metodo='contanti')
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk],
            'righe': [{'data': '2026-09-10', 'descrizione': 'Extra',
                       'importo': '5.00'}],
            'data': '2026-09-18', 'numero': '21/2026',
            'ragione_sociale': 'X'})
        self.assertEqual(r.json()['stato'], 'da_pagare')

    def test_riga_incompleta_rifiutata(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk],
            'righe': [{'data': '2026-09-10', 'descrizione': '',
                       'importo': '5.00'}],
            'data': '2026-09-18', 'numero': '22/2026',
            'ragione_sociale': 'X'})
        self.assertFalse(r.json()['success'])

    def test_elimina_azzera_importo_personalizzato(self):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk], 'importi': {str(o1.pk): '7.00'},
            'data': '2026-09-18', 'numero': '23/2026',
            'ragione_sociale': 'X'})
        fattura_id = r.json()['fattura_id']
        self._post_json(reverse('fatture:elimina', args=[fattura_id]), {})
        o1.refresh_from_db()
        self.assertIsNone(o1.fattura_importo)
        self.assertIsNone(o1.fattura_id)


class VoceManualeTest(BaseFattureTest):
    def test_crea_voce_e_raggruppa(self):
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': self.cliente_a.pk, 'data': '2026-09-10',
            'descrizione': 'Lavaggio completo furgone', 'importo': '35.00'})
        dati = r.json()
        self.assertTrue(dati['success'])
        voce = RigaFattura.objects.get(pk=dati['voce_id'])
        # NON e' un ordine: nessun Ordine creato
        self.assertEqual(Ordine.objects.count(), 0)
        self.assertIsNone(voce.fattura_id)
        self.assertEqual(voce.cliente, self.cliente_a)
        self.assertEqual(voce.importo, Decimal('35.00'))

        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [], 'voci': [voce.pk], 'data': '2026-09-18',
            'numero': '40/2026', 'ragione_sociale': 'X'})
        dati = r.json()
        self.assertTrue(dati['success'])
        voce.refresh_from_db()
        fattura = Fattura.objects.get(pk=dati['fattura_id'])
        self.assertEqual(voce.fattura_id, fattura.pk)
        self.assertEqual(fattura.totale, Decimal('35.00'))
        self.assertEqual(fattura.stato, 'da_pagare')

    def test_voce_torna_in_attesa_se_fattura_eliminata(self):
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': self.cliente_a.pk, 'data': '2026-09-10',
            'descrizione': 'Extra', 'importo': '5.00'})
        voce_id = r.json()['voce_id']
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [], 'voci': [voce_id], 'data': '2026-09-18',
            'numero': '41/2026', 'ragione_sociale': 'X'})
        fattura_id = r.json()['fattura_id']
        self._post_json(reverse('fatture:elimina', args=[fattura_id]), {})
        voce = RigaFattura.objects.get(pk=voce_id)
        self.assertIsNone(voce.fattura_id)
        self.assertEqual(voce.cliente, self.cliente_a)

    def test_elimina_voce_in_attesa(self):
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': self.cliente_a.pk, 'data': '2026-09-10',
            'descrizione': 'Da togliere', 'importo': '5.00'})
        voce_id = r.json()['voce_id']
        r = self._post_json(reverse('fatture:elimina-voce', args=[voce_id]), {})
        self.assertTrue(r.json()['success'])
        self.assertFalse(RigaFattura.objects.filter(pk=voce_id).exists())

    def test_voce_richiede_dati(self):
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': self.cliente_a.pk, 'data': '2026-09-10',
            'descrizione': '', 'importo': '35.00'})
        self.assertFalse(r.json()['success'])
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': 999999, 'data': '2026-09-10',
            'descrizione': 'X', 'importo': '35.00'})
        self.assertFalse(r.json()['success'])

    def test_voce_cliente_diverso_rifiutata(self):
        ordine = _crea_ordine(self.cliente_a, richiede_fattura=True)
        r = self._post_json(reverse('fatture:crea-voce-manuale'), {
            'cliente_id': self.cliente_b.pk, 'data': '2026-09-10',
            'descrizione': 'X', 'importo': '5.00'})
        voce_id = r.json()['voce_id']
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [ordine.pk], 'voci': [voce_id],
            'data': '2026-09-18', 'numero': '42/2026',
            'ragione_sociale': 'X'})
        self.assertFalse(r.json()['success'])


class ModificaFatturaTest(BaseFattureTest):
    def _fattura(self, **kw):
        o1 = _crea_ordine(self.cliente_a, richiede_fattura=True)
        o2 = _crea_ordine(self.cliente_a, richiede_fattura=True,
                          totale='15.00')
        r = self._post_json(reverse('fatture:crea-fattura'), {
            'ordini': [o1.pk, o2.pk], 'data': '2026-09-18',
            'numero': '30/2026', 'ragione_sociale': 'Vecchia SRL'})
        return Fattura.objects.get(pk=r.json()['fattura_id']), o1, o2

    def test_modifica_testata_importi_e_righe(self):
        fattura, o1, o2 = self._fattura()
        r = self._post_json(reverse('fatture:modifica', args=[fattura.pk]), {
            'numero': '31/2026', 'data': '2026-09-19',
            'ragione_sociale': 'Nuova SRL',
            'ordini': [{'id': o1.pk, 'importo': '9.99'},
                       {'id': o2.pk, 'importo': ''}],
            'righe': [{'data': '2026-09-19', 'descrizione': 'Supplemento',
                       'importo': '3.00'}]})
        self.assertTrue(r.json()['success'])
        fattura.refresh_from_db()
        o1.refresh_from_db()
        o2.refresh_from_db()
        self.assertEqual(fattura.numero, '31/2026')
        self.assertEqual(fattura.ragione_sociale, 'Nuova SRL')
        self.assertEqual(o1.fattura_importo, Decimal('9.99'))
        self.assertIsNone(o2.fattura_importo)  # '' = usa il totale
        self.assertEqual(fattura.totale,
                         Decimal('9.99') + Decimal('15.00') + Decimal('3.00'))

    def test_ordine_tolto_torna_da_fatturare(self):
        fattura, o1, o2 = self._fattura()
        r = self._post_json(reverse('fatture:modifica', args=[fattura.pk]), {
            'numero': '30/2026', 'data': '2026-09-18',
            'ragione_sociale': 'Vecchia SRL',
            'ordini': [{'id': o1.pk, 'importo': ''}],
            'righe': []})
        self.assertTrue(r.json()['success'])
        o2.refresh_from_db()
        self.assertIsNone(o2.fattura_id)
        self.assertTrue(o2.richiede_fattura)
        self.assertEqual(fattura.ordini.count(), 1)

    def test_pagata_con_ordine_non_saldato_torna_da_pagare(self):
        fattura, o1, o2 = self._fattura()
        self._post_json(reverse('fatture:segna-pagata', args=[fattura.pk]),
                        {'metodo': 'bonifico'})
        # Ordine nuovo non pagato flaggato, poi aggiunto? La modifica
        # non aggiunge ordini: simuliamo un ordine tornato non saldato
        # cancellando il suo pagamento.
        o1.refresh_from_db()
        o1.pagamenti.all().delete()
        r = self._post_json(reverse('fatture:modifica', args=[fattura.pk]), {
            'numero': '30/2026', 'data': '2026-09-18',
            'ragione_sociale': 'Vecchia SRL',
            'ordini': [{'id': o1.pk, 'importo': ''},
                       {'id': o2.pk, 'importo': ''}],
            'righe': []})
        self.assertTrue(r.json()['success'])
        self.assertEqual(r.json()['stato'], 'da_pagare')

    def test_archiviata_non_modificabile(self):
        fattura, o1, o2 = self._fattura()
        self._post_json(reverse('fatture:segna-pagata', args=[fattura.pk]),
                        {'metodo': 'bonifico'})
        self._post_json(reverse('fatture:archivia', args=[fattura.pk]), {})
        r = self._post_json(reverse('fatture:modifica', args=[fattura.pk]), {
            'numero': 'X/2026', 'data': '2026-09-18',
            'ragione_sociale': 'X', 'ordini': [], 'righe': []})
        self.assertFalse(r.json()['success'])


class NumerazioneTest(BaseFattureTest):
    def test_suggerisci_numero(self):
        self.assertEqual(Fattura.suggerisci_numero(2026), '1/2026')
        Fattura.objects.create(numero='7/2026', data=date(2026, 1, 1),
                               ragione_sociale='X')
        Fattura.objects.create(numero='nota libera', data=date(2026, 1, 1),
                               ragione_sociale='X')
        Fattura.objects.create(numero='3/2025', data=date(2025, 1, 1),
                               ragione_sociale='X')
        self.assertEqual(Fattura.suggerisci_numero(2026), '8/2026')
        self.assertEqual(Fattura.suggerisci_numero(2025), '4/2025')

    def test_api_suggerisci(self):
        r = self.client.get(reverse('fatture:suggerisci-numero'),
                            {'anno': 2026})
        self.assertEqual(r.json()['numero'], '1/2026')


class AccessoTest(TestCase):
    def test_cliente_loggato_rediretto(self):
        user = User.objects.create_user('cli_fatture', 'c@test.it', 'pw')
        Cliente.objects.create(tipo='privato', nome='A', cognome='B',
                               telefono='3330000009', user=user)
        self.client.force_login(user)
        r = self.client.get(reverse('fatture:fatture-list'))
        self.assertEqual(r.status_code, 302)

    def test_endpoint_ok_per_staff(self):
        # Endpoint JSON invece della pagina: il test client di Django 4.2
        # non riesce a copiare il context dei template su Python 3.14
        # (AttributeError in Context.__copy__). Il render della pagina
        # viene collaudato a mano nel browser.
        op = User.objects.create_user('op2_fatture', 'o2@test.it', 'pw',
                                      is_staff=True)
        self.client.force_login(op)
        r = self.client.get(reverse('fatture:suggerisci-numero'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])
