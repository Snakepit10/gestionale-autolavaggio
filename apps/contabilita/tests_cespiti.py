"""Test delle proposte della pagina Cespiti, su dati inventati."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase

from .importa import importa_file
from .models import Cespite, ContoFinanziario, MovimentoFinanziario, VoceConto
from .proposte_cespiti import crea_cespite, proposte, scarta
from .tests import fattura_xml


class ProposteCespitiTest(TestCase):
    def setUp(self):
        self.impianti = VoceConto.objects.get(codice='N.INVESTIMENTI')
        self.banca = ContoFinanziario.objects.get(nome='BAPS')
        self.fattura = importa_file('a.xml', fattura_xml(numero='1')).nuove[0]
        # una riga sul macchinario, l'altra su un costo: nel valore solo la prima
        prima, seconda = self.fattura.righe.all()
        prima.voce = self.impianti
        prima.save()
        seconda.voce = VoceConto.objects.get(codice='CF.ALTRI')
        seconda.save()
        self.pagamento = MovimentoFinanziario.objects.create(conto=self.banca, data=date(2026, 6, 1), importo=Decimal('-500'),
                                                             descrizione='Rata impianto', impronta='prova|rata', voce=self.impianti)

    def test_proposte(self):
        elenco = {p['chiave']: p for p in proposte()}
        self.assertEqual(set(elenco), {f'f{self.fattura.pk}', f'm{self.pagamento.pk}'})
        self.assertEqual(elenco[f'f{self.fattura.pk}']['valore'], Decimal('100.00'))
        self.assertEqual(elenco[f'm{self.pagamento.pk}']['valore'], Decimal('500'))

    def test_piu_proposte_un_cespite_e_ritorno(self):
        dati = {'descrizione': 'Macchinario di prova', 'data_acquisto': date(2026, 10, 15), 'valore': Decimal('600'),
                'anni': 7, 'centro': 'self'}
        cespite = crea_cespite([f'f{self.fattura.pk}', f'm{self.pagamento.pk}'], **dati)
        self.assertEqual((cespite.righe_fattura.count(), cespite.movimenti.count()), (1, 1))
        self.assertEqual(proposte(), [])
        self.assertIsNone(crea_cespite([f'f{self.fattura.pk}'], **dati))     # gia' usata: niente doppioni
        cespite.delete()
        self.assertEqual(len(proposte()), 2)                                  # eliminato, torna proposta

    def test_scartata_non_torna(self):
        self.assertEqual(scarta([f'm{self.pagamento.pk}']), 1)
        self.assertEqual([p['chiave'] for p in proposte()], [f'f{self.fattura.pk}'])


class VisteProposteTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)
        self.fattura = importa_file('a.xml', fattura_xml(numero='1')).nuove[0]
        self.fattura.righe.update(voce=VoceConto.objects.get(codice='N.INVESTIMENTI'))

    def test_pagina_e_creazione(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.urls import resolve
        req = RequestFactory().get('/contabilita/cespiti/')
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        self.assertContains(resolve('/contabilita/cespiti/').func(req), 'Crea un cespite con le righe scelte')

        base = {'azione': 'crea_da_proposte', 'proposte': [f'f{self.fattura.pk}'], 'descrizione': 'Prova', 'anni': '7',
                'centro': 'self', 'data_acquisto': '2026-10-15'}
        self.client.post('/contabilita/cespiti/', {**base, 'valore': ''})          # senza valore non si crea
        self.assertEqual(Cespite.objects.count(), 0)
        self.client.post('/contabilita/cespiti/', {**base, 'valore': '150,00'})
        c = Cespite.objects.get()
        self.assertEqual((c.valore, c.data_acquisto, c.righe_fattura.count()), (Decimal('150.00'), date(2026, 10, 15), 2))
