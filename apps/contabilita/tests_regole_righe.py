"""Regole per riga dei fornitori: una fattura con merce del self e del
servito si divide da sola sui centri. Dati inventati."""
from django.contrib.auth.models import Group, User
from django.test import TestCase

from .importa import importa_file
from .models import RegolaFornitore, RegolaRigaFornitore, VoceConto
from .tests import fattura_xml

RIGHE = (('CERA PORTALI 25 KG', '100.00', '22.00'), ('Shampoo  lavaggio a mano', '60.00', '22.00'),
         ('Panni microfibra', '40.00', '22.00'), ('Trasporto', '10.00', '22.00'))


def fattura(numero='1'):
    return importa_file('a.xml', fattura_xml(numero=numero, righe=RIGHE, imponibile='210.00', imposta='46.20',
                                             totale='256.20')).nuove[0]


def voce(codice):
    return VoceConto.objects.get(codice=codice)


class RegoleRigheTest(TestCase):
    def setUp(self):
        self.f = fattura()
        self.fornitore = self.f.fornitore
        RegolaFornitore.objects.create(fornitore=self.fornitore, voce=voce('CF.ALTRI'))
        RegolaRigaFornitore.objects.create(fornitore=self.fornitore, testo='cera, schiuma', ripartizione={'self': 100})
        RegolaRigaFornitore.objects.create(fornitore=self.fornitore, testo='shampoo lavaggio', voce=voce('CV.CHIMICI'),
                                           ripartizione={'servito': 100})
        RegolaRigaFornitore.objects.create(fornitore=self.fornitore, testo='shampoo', voce=voce('CV.CHIMICI'),
                                           ripartizione={'self': 100})
        RegolaRigaFornitore.objects.create(fornitore=self.fornitore, testo='microfibra', voce=voce('CV.CONSUMO'),
                                           ripartizione={'self': 50, 'servito': 50})

    def apri(self, utente, url):
        """GET con RequestFactory: il client dei test non rende i template su Python 3.14."""
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from django.urls import resolve
        req = RequestFactory().get(url)
        req.user, req.session = utente, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url)
        return trovata.func(req, *trovata.args, **trovata.kwargs)

    def righe(self, f):
        return [(r.voce.codice if r.voce else None, r.ripartizione) for r in f.righe.order_by('numero_linea')]

    def test_import_divide_le_righe(self):
        self.assertEqual(self.righe(fattura('2')), [
            ('CF.ALTRI', {'self': 100}),                        # senza voce: quella della regola generale
            ('CV.CHIMICI', {'servito': 100}),                   # vince la parola piu' lunga, spazi normalizzati
            ('CV.CONSUMO', {'self': 50, 'servito': 50}),
            ('CF.ALTRI', {}),                                   # nessuna parola: regola generale
        ])

    def test_senza_regola_generale_la_riga_resta_da_classificare(self):
        RegolaFornitore.objects.all().delete()
        f = fattura('3')
        self.assertEqual(self.righe(f)[0], (None, {'self': 100}))
        self.assertEqual(self.righe(f)[3], (None, {}))
        self.assertFalse(f.classificata)

    def test_riapplica_dalla_pagina(self):
        from apps.auth_system.models import PermessoGruppo
        titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(titolare)
        url = f'/contabilita/fatture/{self.f.pk}/'
        # la fattura e' stata importata prima delle regole: righe vuote
        self.assertEqual(self.righe(self.f)[1], (None, {}))
        for pagina in (url, '/contabilita/piano-dei-conti/'):
            self.assertContains(self.apri(titolare, pagina), 'shampoo lavaggio')
        self.client.post(url, {'azione': 'regola_riga', 'testo': ' trasporto ,, ', 'voce': voce('CF.ALTRI').pk,
                               'rrip_comune': '100'})
        self.assertEqual(RegolaRigaFornitore.objects.get(testo='trasporto').ripartizione, {'comune': 100})
        self.client.post(url, {'azione': 'regola_riga', 'testo': 'niente'})       # serve voce o ripartizione
        self.assertFalse(RegolaRigaFornitore.objects.filter(testo='niente').exists())
        altra = fattura('4')
        altra.righe.update(voce=None, ripartizione={})
        altra.aggiorna_classificata()
        self.client.post(url, {'azione': 'riapplica', 'tutte': '1'})
        for f in (self.f, altra):
            f.refresh_from_db()
            self.assertTrue(f.classificata)
            self.assertEqual(self.righe(f)[1], ('CV.CHIMICI', {'servito': 100}))
            self.assertEqual(self.righe(f)[3], ('CF.ALTRI', {'comune': 100}))
