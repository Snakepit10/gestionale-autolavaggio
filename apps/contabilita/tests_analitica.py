"""Test della contabilita' analitica su un mese inventato: ripartizioni
delle voci, delle righe e del personale, direct costing del self-service,
full costing del servito, quadratura col conto economico."""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from .analitica import analitica, dividi
from .conto_economico import conto_economico
from .importa import importa_file
from .models import Cespite, ParametriAnalitica, RipartizionePersonale, VoceConto
from .tests import fattura_xml

OTTOBRE = date(2026, 10, 1)


def fattura(numero, imponibile, codice, ripartizione_riga=None):
    f = importa_file(f'{numero}.xml', fattura_xml(numero=numero, righe=(('Voce', imponibile, '22.00'),),
                                                    imponibile=imponibile, imposta='0.00', totale=imponibile,
                                                    pagamenti=())).nuove[0]
    f.righe.update(voce=VoceConto.objects.get(codice=codice), ripartizione=ripartizione_riga or {})
    return f


class DividiTest(TestCase):
    def test_arrotondamento(self):
        quote = dividi(Decimal('100'), {'self': 33.33, 'servito': 33.33, 'prodotti': 33.34})
        self.assertEqual(sum(quote.values()), Decimal('100'))
        self.assertEqual(dividi(Decimal('10'), {}), {'comune': Decimal('10')})


class AnaliticaTest(TestCase):
    def setUp(self):
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.ordini.models import Ordine
        from apps.personale.models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese
        # ricavi: servito 100 netti, portali 200 netti con 20 cicli
        ordine = Ordine.objects.create(totale=Decimal('122'), totale_finale=Decimal('122'))
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)))
        portale = Cassa.objects.create(nome='Portale prova', tipo='automatica', tracking_washcycles=True)
        ChiusuraCassaAutomatica.objects.create(cassa=portale, data=date(2026, 10, 6), vendita_contante=Decimal('244'),
                                               incasso_totale=Decimal('244'), wash_cycles=20)
        # acqua 70/30 dalla voce, chimici tutto self sulla riga, affitto sul comune
        acqua = VoceConto.objects.get(codice='CV.ACQUA')
        acqua.ripartizione = {'self': 70, 'servito': 30}
        acqua.save()
        fattura('1', '100.00', 'CV.ACQUA')
        fattura('2', '50.00', 'CV.CHIMICI', {'self': 100})
        fattura('3', '300.00', 'CF.AFFITTO')
        # personale: 8 ore a 10 euro, 75% servito e 25% self
        d = Dipendente.objects.create(nome='Prova', tipo='occasionale')
        ParametroRetributivo.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), tariffa_oraria=Decimal('10'),
                                            coefficiente_lordo=Decimal('1'))
        RigaMese.objects.create(mese=MesePersonale.objects.create(mese=OTTOBRE), dipendente=d)
        Presenza.objects.create(dipendente=d, data=date(2026, 10, 2), ore=Decimal('8'))
        RipartizionePersonale.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), ripartizione={'servito': 75, 'self': 25})
        # portale da 1200 in un anno: 100 al mese sul self
        Cespite.objects.create(descrizione='Portale', data_acquisto=date(2026, 10, 1), valore=Decimal('1200'), anni=1, centro='self')

    def test_direct_costing_self(self):
        s = analitica([OTTOBRE])['self']
        self.assertEqual(s['ricavi'], Decimal('200.00'))
        self.assertEqual(s['variabili'], Decimal('120.00'))          # acqua 70 + chimici 50
        self.assertEqual(s['mdc1'], Decimal('80.00'))
        self.assertEqual(s['fissi_specifici'], Decimal('120.00'))    # personale 20 + ammortamento 100
        self.assertEqual(s['mdc2'], Decimal('-40.00'))
        self.assertEqual((s['cicli'], s['ricavo_ciclo'], s['variabile_ciclo']), (20, Decimal('10'), Decimal('6')))
        self.assertEqual(s['cicli_pareggio'], Decimal('30'))         # 120 / (80 / 20)

    def test_full_costing_servito(self):
        s = analitica([OTTOBRE])['servito']
        self.assertEqual(s['ricavi'], Decimal('100.00'))
        self.assertEqual(s['diretti'], Decimal('90.00'))             # acqua 30 + personale 60
        self.assertEqual(s['comuni'], Decimal('300.00'))
        self.assertEqual(s['quota_comuni'], Decimal('100.00'))       # ricavi 100 su 300
        self.assertEqual(s['risultato'], Decimal('-90.00'))
        self.assertEqual((s['lavaggi'], s['costo_lavaggio']), (1, Decimal('190.00')))
        self.assertEqual(s['ore'], Decimal('6'))
        ParametriAnalitica.objects.create(criterio_comuni='percentuale', percentuale_servito=Decimal('50'))
        self.assertEqual(analitica([OTTOBRE])['servito']['quota_comuni'], Decimal('150.00'))

    def test_quadra_col_conto_economico(self):
        a = analitica([OTTOBRE])
        ebit = next(r['totale'] for r in conto_economico([OTTOBRE])['righe'] if r['chiave'] == 'ebit')
        self.assertEqual(a['quadro'][-1]['totale'], ebit)
        self.assertEqual(ebit, Decimal('-330.00'))


class VisteAnaliticaTest(TestCase):
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

    def test_pagine_e_ripartizione_personale(self):
        from apps.personale.models import Dipendente
        d = Dipendente.objects.create(nome='Prova')
        for url in ('/contabilita/analitica/?anno=2026', '/contabilita/analitica/?anno=2026&mese=10',
                    '/contabilita/analitica/impostazioni/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'senza ripartizione')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'personale', 'dal': '2026-10',
                                                                  f'rip_{d.pk}_servito': '80', f'rip_{d.pk}_self': '20'})
        r = RipartizionePersonale.objects.get()
        self.assertEqual((r.valido_dal, r.ripartizione), (OTTOBRE, {'servito': 80, 'self': 20}))
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'parametri', 'criterio_comuni': 'percentuale',
                                                                  'percentuale_servito': '60'})
        self.assertEqual(ParametriAnalitica.attuali().percentuale_servito, Decimal('60'))
