"""Pagamenti di fatture con un importo diverso dalla scadenza (es. la
commissione dentro il bonifico): la differenza e' un costo sulla sua voce,
il resto resta "Pagamento fornitori". Dati inventati."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase

from .conto_economico import conto_economico, flussi_di_cassa
from .importa import importa_file
from .models import ContoFinanziario, MovimentoFinanziario, VoceConto
from .prima_nota import abbina, proponi_abbinamenti, togli_abbinamento
from .tests import fattura_xml

NOVEMBRE = date(2026, 11, 1)


def voce(codice):
    return VoceConto.objects.get(codice=codice)


class CommissioniTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)
        self.banca = ContoFinanziario.objects.get(nome='BAPS')
        self.scadenza = importa_file('a.xml', fattura_xml()).nuove[0].scadenze.get()      # 183,00

    def movimento(self, importo, descrizione):
        return MovimentoFinanziario.objects.create(conto=self.banca, data=date(2026, 11, 20), importo=Decimal(importo),
                                                   descrizione=descrizione, impronta=f'{importo}|{descrizione}')

    def apri(self, url):
        """GET con RequestFactory: il client dei test non rende i template su Python 3.14."""
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from django.urls import resolve
        req = RequestFactory().get(url)
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url.split('?')[0])
        return trovata.func(req, *trovata.args, **trovata.kwargs)

    def test_proposta_con_commissione_solo_col_nome(self):
        senza_nome = self.movimento('-184.50', 'Bonifico SEPA')
        troppo = self.movimento('-190.00', 'Bonifico a Detergenti')
        self.assertEqual(proponi_abbinamenti(), [])
        con_nome = self.movimento('-184.50', 'Bonifico a Detergenti comm. 1,50')
        [p] = proponi_abbinamenti()
        self.assertEqual((p['movimento'], p['commissione']), (con_nome, Decimal('1.50')))
        esatto = self.movimento('-183.00', 'Bonifico SEPA')                    # l'importo esatto vince
        [p] = proponi_abbinamenti()
        self.assertEqual((p['movimento'], p['commissione']), (esatto, Decimal('0')))
        self.assertTrue(senza_nome and troppo)

    def test_abbina_tutti_conto_economico_e_flussi(self):
        bonifico = self.movimento('-184.50', 'Bonifico a Detergenti')
        self.client.post('/contabilita/prima-nota/', {'azione': 'abbina_tutte'})
        bonifico.refresh_from_db()
        self.assertEqual((bonifico.voce.codice, bonifico.differenza, bonifico.voce_differenza.codice),
                         ('N.FORNITORI', Decimal('-1.50'), 'CF.BANCA'))
        ce = {r['chiave']: r['totale'] for r in conto_economico([NOVEMBRE])['righe'] if r['chiave']}
        self.assertEqual(ce[f'voce:{voce("CF.BANCA").pk}'], Decimal('1.50'))
        self.assertNotIn(f'voce:{voce("N.FORNITORI").pk}', ce)
        fc = {r['chiave']: r['totale'] for r in flussi_di_cassa([NOVEMBRE])['righe'] if r['chiave']}
        self.assertEqual(fc[f'voce:{voce("N.FORNITORI").pk}'], Decimal('-183.00'))
        self.assertEqual(fc[f'voce:{voce("CF.BANCA").pk}'], Decimal('-1.50'))
        for url in (f'/contabilita/prima-nota/{bonifico.pk}/',
                    f'/contabilita/conto-economico/dettaglio/?chiave=voce:{voce("CF.BANCA").pk}&anno=2026'):
            self.assertContains(self.apri(url), 'Detergenti')
        togli_abbinamento(self.scadenza.__class__.objects.get(pk=self.scadenza.pk))
        bonifico.refresh_from_db()
        self.assertEqual((bonifico.voce, bonifico.differenza, bonifico.voce_differenza), (None, 0, None))

    def test_abbina_a_mano_con_la_voce_della_differenza(self):
        bonifico = self.movimento('-170.00', 'Bonifico')                       # abbuono di 13 dal fornitore
        url = f'/contabilita/prima-nota/{bonifico.pk}/'
        self.client.post(url, {'azione': 'abbina', 'scadenza': [self.scadenza.pk]})
        self.client.post(url, {'azione': 'abbina', 'scadenza': [self.scadenza.pk], 'voce_differenza': voce('N.IVA').pk})
        self.assertFalse(bonifico.scadenze.exists())                           # senza voce o non economica: niente
        self.client.post(url, {'azione': 'abbina', 'scadenza': [self.scadenza.pk], 'voce_differenza': voce('CV.CHIMICI').pk})
        bonifico.refresh_from_db()
        self.assertEqual((bonifico.differenza, bonifico.voce_differenza.codice), (Decimal('13.00'), 'CV.CHIMICI'))
        ce = {r['chiave']: r['totale'] for r in conto_economico([NOVEMBRE])['righe'] if r['chiave']}
        self.assertEqual(ce[f'voce:{voce("CV.CHIMICI").pk}'], Decimal('-13.00'))     # riduce il costo di novembre

    def test_importo_esatto_ignora_la_voce(self):
        bonifico = self.movimento('-183.00', 'Bonifico')
        abbina(bonifico, [self.scadenza], voce('CF.BANCA'))
        self.assertEqual((bonifico.differenza, bonifico.voce_differenza), (0, None))
