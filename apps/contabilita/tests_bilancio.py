"""Test del conto economico e dei flussi di cassa su un mese inventato:
nessun doppio conteggio fra fatture, pagamenti, incassi POS e giroconti."""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from .conto_economico import conto_economico, flussi_di_cassa, quote_competenza
from .importa import importa_file
from .models import Cespite, ContoFinanziario, FatturaPassiva, MovimentoFinanziario, VoceConto
from .prima_nota import abbina, giroconto
from .tests import fattura_xml

OTTOBRE = date(2026, 10, 1)


def voce(codice):
    return VoceConto.objects.get(codice=codice)


def movimento(conto, importo, codice=None, data=date(2026, 10, 10), descrizione=''):
    return MovimentoFinanziario.objects.create(
        conto=conto, data=data, importo=Decimal(importo), descrizione=descrizione or codice or '',
        voce=voce(codice) if codice else None, impronta=f'{importo}|{codice}|{data}|{descrizione}')


def per_chiave(righe):
    return {r['chiave']: r for r in righe if r['chiave']}


class CompetenzaTest(TestCase):
    def test_quote(self):
        q = quote_competenza(Decimal('100'), date(2026, 10, 1), date(2026, 12, 1))
        self.assertEqual(list(q.values()), [Decimal('33.33'), Decimal('33.33'), Decimal('33.34')])

    def test_cespite(self):
        c = Cespite(descrizione='Portale', data_acquisto=date(2026, 3, 15), valore=Decimal('1000'), anni=1)
        self.assertEqual(c.quota(date(2026, 2, 1)), 0)
        self.assertEqual(c.quota(date(2026, 3, 1)), Decimal('83.33'))
        self.assertEqual(c.quota(date(2027, 2, 1)), Decimal('1000') - Decimal('83.33') * 11)
        self.assertEqual(c.quota(date(2027, 3, 1)), 0)
        self.assertEqual(c.ammortizzato_al(date(2027, 6, 1)), Decimal('1000'))


class ContoEconomicoTest(TestCase):
    def setUp(self):
        from apps.ordini.models import Ordine
        self.banca = ContoFinanziario.objects.get(nome='BAPS')
        self.banca.saldo_iniziale = Decimal('1000')
        self.banca.save()
        self.cassa = ContoFinanziario.objects.get(nome='Cassa contanti')
        # ricavi: un ordine servito da 122 (100 + IVA)
        ordine = Ordine.objects.create(totale=Decimal('122'), totale_finale=Decimal('122'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)))
        # fattura di chimici da 200 su ottobre e novembre, pagata con un bonifico
        f = importa_file('a.xml', fattura_xml(righe=(('Shampoo', '200.00', '22.00'),), imponibile='200.00',
                                              imposta='44.00', totale='244.00', pagamenti=(('2026-10-20', '244.00'),))).nuove[0]
        FatturaPassiva.objects.filter(pk=f.pk).update(competenza_al=date(2026, 11, 1))
        f.righe.update(voce=voce('CV.CHIMICI'))
        pagamento = movimento(self.banca, '-244', descrizione='Bonifico Detergenti', data=date(2026, 10, 20))
        abbina(pagamento, list(f.scadenze.all()))
        # fattura da 50 non classificata
        importa_file('b.xml', fattura_xml(numero='2', righe=(('Varie', '50.00', '22.00'),), imponibile='50.00',
                                          imposta='11.00', totale='61.00', pagamenti=()))
        # banca: commissioni e interessi senza fattura, incassi POS, giroconto dalla cassa
        movimento(self.banca, '-10', 'CF.BANCA')
        movimento(self.banca, '-5', 'OF.INTERESSI')
        movimento(self.banca, '500', 'N.INCASSI')
        versamento = movimento(self.cassa, '-300', descrizione='Versamento in banca')
        giroconto(versamento, self.banca)
        # cespite da 1200 in 1 anno da ottobre: 100 al mese
        Cespite.objects.create(descrizione='Aspirapolvere', data_acquisto=date(2026, 10, 3), valore=Decimal('1200'), anni=1)
        # personale: 8 ore a 10 euro, occasionale -> 80
        from apps.personale.models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese
        d = Dipendente.objects.create(nome='Prova', tipo='occasionale')
        ParametroRetributivo.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), tariffa_oraria=Decimal('10'),
                                            coefficiente_lordo=Decimal('1'))
        RigaMese.objects.create(mese=MesePersonale.objects.create(mese=OTTOBRE), dipendente=d)
        Presenza.objects.create(dipendente=d, data=date(2026, 10, 2), ore=Decimal('8'))

    def test_conto_economico_ottobre(self):
        ce = conto_economico([OTTOBRE])
        r = {k: v['totale'] for k, v in per_chiave(ce['righe']).items()}
        self.assertEqual(r['ricavi:servito'], Decimal('100.00'))
        self.assertEqual(r['tot:ricavi'], Decimal('100.00'))                # gli incassi POS non sono ricavi
        self.assertEqual(r[f'voce:{voce("CV.CHIMICI").pk}'], Decimal('100.00'))   # meta' fattura in ottobre
        self.assertEqual(r['margine'], Decimal('0.00'))
        self.assertEqual(r['dacl'], Decimal('50.00'))
        self.assertEqual(r['tot:costi_fissi'], Decimal('60.00'))            # 50 da classificare + 10 commissioni
        self.assertEqual(r['personale'], Decimal('80'))
        self.assertEqual(r['ebitda'], Decimal('-140.00'))
        self.assertEqual(r['ammortamenti'], Decimal('100.00'))
        self.assertEqual(r['ebit'], Decimal('-240.00'))
        self.assertEqual(r['tot:oneri_finanziari'], Decimal('5'))
        self.assertEqual(r['netto'], Decimal('-245.00'))                    # il bonifico della fattura non conta
        novembre = per_chiave(conto_economico([date(2026, 11, 1)])['righe'])
        self.assertEqual(novembre[f'voce:{voce("CV.CHIMICI").pk}']['totale'], Decimal('100.00'))

    def test_flussi_ottobre(self):
        fc = flussi_di_cassa([OTTOBRE])
        r = {k: v['totale'] for k, v in per_chiave(fc['righe']).items()}
        self.assertEqual(r['iniziale'], Decimal('1000'))
        self.assertEqual(r['flusso:operativo'], Decimal('246'))             # 500 - 244 - 10
        self.assertEqual(r['flusso:finanziario'], Decimal('-5'))
        self.assertEqual(r['netto'], Decimal('241'))
        self.assertNotIn('giroconti', r)                                    # -300 cassa, +300 banca: si annullano
        self.assertEqual(r['finale'], r['iniziale'] + r['netto'])


class VisteBilancioTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)

    def apri(self, url):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from django.urls import resolve
        req = RequestFactory().get(url)
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url.split('?')[0])
        return trovata.func(req, *trovata.args, **trovata.kwargs)

    def test_pagine(self):
        importa_file('a.xml', fattura_xml())
        chimici = voce('CV.CHIMICI')
        for url in ('/contabilita/conto-economico/?anno=2026', '/contabilita/conto-economico/?anno=2026&vista=trimestri&confronto=1',
                    '/contabilita/flussi-di-cassa/?anno=2026', '/contabilita/flussi-di-cassa/?anno=2026&vista=trimestri',
                    f'/contabilita/conto-economico/dettaglio/?chiave=voce:{chimici.pk}&anno=2026',
                    '/contabilita/conto-economico/dettaglio/?chiave=dacl&anno=2026', '/contabilita/cespiti/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/conto-economico/?anno=2026'), 'Fatture da classificare')

    def test_nuovo_cespite(self):
        self.client.post('/contabilita/cespiti/', {'azione': 'salva', 'descrizione': 'Portale', 'data_acquisto': '2026-01-10',
                                                   'valore': '60.000,00', 'anni': '10', 'centro': 'self'})
        c = Cespite.objects.get()
        self.assertEqual((c.valore, c.quota_mensile, c.centro), (Decimal('60000.00'), Decimal('500.00'), 'self'))
