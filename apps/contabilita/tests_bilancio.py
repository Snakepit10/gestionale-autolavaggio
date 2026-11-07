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
        # lo scontrino dell'ordine al registratore fiscale: 122 -> 100 netti
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        ChiusuraCassaAutomatica.objects.create(cassa=Cassa.objects.create(nome='Registratore prova', tipo='automatica',
                                                                          modalita_registratore=True),
                                               data=date(2026, 10, 6), incasso_totale=Decimal('122'))
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
        ce = conto_economico([OTTOBRE], 'scontrini')
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
        novembre = per_chiave(conto_economico([date(2026, 11, 1)], 'scontrini')['righe'])
        self.assertEqual(novembre[f'voce:{voce("CV.CHIMICI").pk}']['totale'], Decimal('100.00'))

    def test_pagamenti_senza_fattura(self):
        from .conto_economico import controlli
        tipi = lambda: {a[2]: a[1] for a in controlli([OTTOBRE])}
        self.assertNotIn('fornitori_senza_fattura', tipi())            # il bonifico abbinato non conta
        movimento(self.banca, '-1220.50', 'N.FORNITORI', descrizione='Bonifico Energia')
        movimento(self.banca, '30', 'N.FORNITORI', descrizione='Rimborso fornitore')
        self.assertIn('1 pagamenti fornitori', tipi()['fornitori_senza_fattura'])
        self.assertIn('€1.220,50', tipi()['fornitori_senza_fattura'])
        self.assertEqual(conto_economico([OTTOBRE], 'scontrini')['kpi']['netto'], Decimal('-245.00'))  # resta fuori dal CE

    def test_flussi_ottobre(self):
        fc = flussi_di_cassa([OTTOBRE])
        r = {k: v['totale'] for k, v in per_chiave(fc['righe']).items()}
        self.assertEqual(r['iniziale'], Decimal('1000'))
        self.assertEqual(r['flusso:operativo'], Decimal('246'))             # 500 - 244 - 10
        self.assertEqual(r['flusso:finanziario'], Decimal('-5'))
        self.assertEqual(r['netto'], Decimal('241'))
        self.assertNotIn('giroconti', r)                                    # -300 cassa, +300 banca: si annullano
        self.assertEqual(r['finale'], r['iniziale'] + r['netto'])


class RicaviRilevatoTest(TestCase):
    def test_rilevato_reale_meno_iva_corrispettivi(self):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica, QuadraturaGiornaliera, SpesaCassa
        from apps.ordini.models import Ordine
        giorno = date(2026, 10, 6)
        # rilevato: 300 contanti + 50 POS - 100 fondo + 20 spesa di cassa = 270
        QuadraturaGiornaliera.objects.create(data=giorno, contanti_totali=Decimal('300'), lettore_carte_servito=Decimal('50'),
                                             fondo_cassa_iniziale=Decimal('100'))
        SpesaCassa.objects.create(data=giorno, importo=Decimal('20'), descrizione='Prova', categoria='altro')
        # corrispettivi: self service 122 + registratore 61 = 183 -> IVA 33
        portale = Cassa.objects.create(nome='Portale prova', tipo='automatica', tracking_washcycles=True)
        registratore = Cassa.objects.create(nome='Registratore prova', tipo='automatica', modalita_registratore=True)
        ChiusuraCassaAutomatica.objects.create(cassa=portale, data=giorno, vendita_contante=Decimal('122'), incasso_totale=Decimal('122'))
        ChiusuraCassaAutomatica.objects.create(cassa=registratore, data=giorno, incasso_totale=Decimal('61'))
        # un ordine servito non pagato non cambia il rilevato
        ordine = Ordine.objects.create(totale=Decimal('1000'), totale_finale=Decimal('1000'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)))

        ce = conto_economico([OTTOBRE], base='rilevato')
        r = {k: v['totale'] for k, v in per_chiave(ce['righe']).items()}
        # servito: ordini 1000 meno l'IVA del registratore (61 -> 11); portali: il resto 270 - 1000 meno l'IVA dei portali (22)
        self.assertEqual(r['ricavi:servito'], Decimal('989.00'))
        self.assertEqual(r['ricavi:portali'], Decimal('-752.00'))
        self.assertNotIn('ricavi:prodotti', r)
        self.assertEqual(r['tot:ricavi'], Decimal('237.00'))                  # rilevato 270 - IVA 33
        self.assertEqual(ce['dettaglio_rilevato'], {'rilevato': Decimal('270'), 'iva_registratore': Decimal('11.00'),
                                                    'iva_portali': Decimal('22.00')})
        self.assertEqual(ce['giorni_rilevati'], [1])
        fiscale = {k: v['totale'] for k, v in per_chiave(conto_economico([OTTOBRE], base='scontrini')['righe']).items()}
        self.assertEqual(fiscale['ricavi:servito'], Decimal('50.00'))         # registratore: 61 / 1,22, non gli ordini
        self.assertEqual(fiscale['ricavi:portali'], Decimal('100.00'))        # chiusura del portale: 122 / 1,22
        self.assertEqual(fiscale['tot:ricavi'], Decimal('150.00'))


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
                    '/contabilita/conto-economico/?anno=2026&ricavi=scontrini&vista=trimestri',
                    '/contabilita/prima-nota/?stato=senza_fattura&anno=2026', '/contabilita/prima-nota/?stato=senza_fattura',
                    '/contabilita/flussi-di-cassa/?anno=2026', '/contabilita/flussi-di-cassa/?anno=2026&vista=trimestri',
                    '/contabilita/flussi-di-cassa/?anno=2026&contanti=scontrini',
                    f'/contabilita/conto-economico/dettaglio/?chiave=voce:{chimici.pk}&anno=2026',
                    '/contabilita/conto-economico/dettaglio/?chiave=dacl&anno=2026', '/contabilita/cespiti/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/conto-economico/?anno=2026'), 'Fatture da classificare')

    def test_nuovo_cespite(self):
        self.client.post('/contabilita/cespiti/', {'azione': 'salva', 'descrizione': 'Portale', 'data_acquisto': '2026-01-10',
                                                   'valore': '60.000,00', 'anni': '10', 'centro': 'self'})
        c = Cespite.objects.get()
        self.assertEqual((c.valore, c.quota_mensile, c.centro), (Decimal('60000.00'), Decimal('500.00'), 'self'))
