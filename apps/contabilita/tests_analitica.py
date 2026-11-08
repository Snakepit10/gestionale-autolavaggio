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
from .models import Cespite, RipartizionePersonale, VoceConto
from .tests import fattura_xml

OTTOBRE = date(2026, 10, 1)
ZERO = Decimal('0.00')


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
        from apps.core.models import Categoria, ServizioProdotto
        from apps.finanze.models import Cassa, ChiusuraCassaAutomatica
        from apps.ordini.models import ItemOrdine, Ordine
        from apps.personale.models import Dipendente, MesePersonale, ParametroRetributivo, Presenza, RigaMese
        # ricavi: servito 100 netti (un lavaggio a mano, senza portale), portali 200 netti con 20 cicli
        ordine = Ordine.objects.create(totale=Decimal('122'), totale_finale=Decimal('122'))
        self.categoria = Categoria.objects.create(nome='Servito')
        a_mano = ServizioProdotto.objects.create(titolo='Lavaggio a mano', categoria=self.categoria, prezzo=Decimal('122'),
                                                 descrizione='', programmi_portale='')
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=a_mano, quantita=1, prezzo_unitario=a_mano.prezzo)
        Ordine.objects.filter(pk=ordine.pk).update(data_ora=timezone.make_aware(datetime(2026, 10, 6, 10, 0)),
                                                   totale=Decimal('122'), totale_finale=Decimal('122'))
        registratore = Cassa.objects.create(nome='Registratore prova', tipo='automatica', modalita_registratore=True)
        ChiusuraCassaAutomatica.objects.create(cassa=registratore, data=date(2026, 10, 6), incasso_totale=Decimal('122'))
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
        RigaMese.objects.create(mese=MesePersonale.objects.create(mese=OTTOBRE), dipendente=d, busta_paga=Decimal('80'))  # tutto in busta
        Presenza.objects.create(dipendente=d, data=date(2026, 10, 2), ore=Decimal('8'))
        RipartizionePersonale.objects.create(dipendente=d, valido_dal=date(2026, 1, 1), ripartizione={'servito': 75, 'self': 25})
        # portale da 1200 in un anno: 100 al mese sul self
        Cespite.objects.create(descrizione='Portale', data_acquisto=date(2026, 10, 1), valore=Decimal('1200'), anni=1, centro='self')

    def test_direct_costing_self(self):
        s = analitica([OTTOBRE], 'scontrini')['self']
        self.assertEqual(s['ricavi'], Decimal('200.00'))
        self.assertEqual(s['variabili'], Decimal('120.00'))          # acqua 70 + chimici 50
        self.assertEqual(s['mdc1'], Decimal('80.00'))
        self.assertEqual(s['fissi_specifici'], Decimal('120.00'))    # personale 20 + ammortamento 100
        self.assertEqual(s['mdc2'], Decimal('-40.00'))
        self.assertEqual((s['cicli'], s['ricavo_ciclo'], s['variabile_ciclo']), (20, Decimal('10'), Decimal('6')))
        self.assertEqual(s['cicli_pareggio'], Decimal('30'))         # 120 / (80 / 20)

    def aggiungi_portale(self, quantita, chiuso_senza_portale=0):
        """Servizi del servito con programmi portale: un ciclo per pezzo."""
        from apps.core.models import ServizioProdotto
        from apps.finanze.models import LavaggioSenzaPortale
        from apps.ordini.models import ItemOrdine, Ordine
        completo = ServizioProdotto.objects.create(titolo='Lavaggio Completo - Media', categoria=self.categoria,
                                                   prezzo=Decimal('0'), descrizione='', programmi_portale='1,2')
        ordine = Ordine.objects.get()
        ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=completo, quantita=quantita, prezzo_unitario=0)
        for _ in range(chiuso_senza_portale):
            item = ItemOrdine.objects.create(ordine=ordine, servizio_prodotto=completo, quantita=1, prezzo_unitario=0)
            LavaggioSenzaPortale.objects.create(item=item)
        Ordine.objects.filter(pk=ordine.pk).update(totale=Decimal('122'), totale_finale=Decimal('122'))

    def test_cicli_self_tolgono_il_servito(self):
        """I servizi con programmi portale passano dal portale: non sono cicli self.
        Quelli chiusi senza portale non contano."""
        self.aggiungi_portale(4, chiuso_senza_portale=1)
        s = analitica([OTTOBRE], 'scontrini')['self']
        self.assertEqual((s['cicli_totali'], s['cicli_servito'], s['cicli']), (20, 4, 16))
        self.assertEqual(s['ricavo_ciclo'], Decimal('12.5'))         # 200 / 16

    def test_lavaggi_senza_vendite_di_abbonamenti(self):
        """La vendita di un abbonamento (ordine senza righe) non e' un lavaggio;
        l'accesso in abbonamento (servizio a 0 euro) si'."""
        from apps.core.models import ServizioProdotto
        from apps.ordini.models import ItemOrdine, Ordine
        quando = timezone.make_aware(datetime(2026, 10, 7, 9, 0))
        vendita = Ordine.objects.create(totale=Decimal('50'), totale_finale=Decimal('50'), origine='abbonamento')
        accesso = Ordine.objects.create(totale=0, totale_finale=0, origine='abbonamento')
        ItemOrdine.objects.create(ordine=accesso, servizio_prodotto=ServizioProdotto.objects.get(titolo='Lavaggio a mano'),
                                  quantita=1, prezzo_unitario=0)
        Ordine.objects.filter(pk__in=(vendita.pk, accesso.pk)).update(data_ora=quando)
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual((s['lavaggi'], s['lavaggi_abbonamento']), (2, 1))

    def test_full_costing_servito(self):
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual(s['ricavi'], Decimal('100.00'))
        self.assertEqual(s['diretti'], Decimal('90.00'))             # acqua 30 + personale 60
        self.assertEqual(s['comuni'], Decimal('300.00'))
        self.assertEqual(s['quota_comuni'], Decimal('100.00'))       # affitto per ricavi: 100 su 300
        self.assertEqual(s['risultato'], Decimal('-90.00'))
        self.assertEqual((s['lavaggi'], s['costo_lavaggio']), (1, Decimal('190.00')))
        self.assertEqual(s['ore'], Decimal('6'))

    def test_driver_dei_costi_comuni(self):
        affitto = VoceConto.objects.get(codice='CF.AFFITTO')
        # ore: 2 self, 6 servito
        affitto.driver_comuni = 'ore'
        affitto.save()
        a = analitica([OTTOBRE], 'scontrini')
        self.assertEqual((a['self']['quota_comuni'], a['servito']['quota_comuni']), (Decimal('75.00'), Decimal('225.00')))
        # cicli: nessun ciclo del servito, tutto al self
        affitto.driver_comuni = 'cicli'
        affitto.save()
        a = analitica([OTTOBRE], 'scontrini')
        self.assertEqual((a['self']['quota_comuni'], a['servito']['quota_comuni']), (Decimal('300.00'), ZERO))
        self.assertEqual(a['self']['costo_ciclo_portale'], Decimal('15'))   # 300 / 20 cicli
        # 16 cicli self e 4 del servito: il servito paga 4 cicli a 15 euro
        self.aggiungi_portale(4)
        s = analitica([OTTOBRE], 'scontrini')['servito']
        self.assertEqual((s['quota_comuni'], s['cicli_portale'], s['costo_cicli_portale']),
                         (Decimal('60.00'), 4, Decimal('60.00')))

    def test_quadra_col_conto_economico(self):
        a = analitica([OTTOBRE], 'scontrini')
        ebit = next(r['totale'] for r in conto_economico([OTTOBRE], 'scontrini')['righe'] if r['chiave'] == 'ebit')
        self.assertEqual(a['quadro'][-1]['totale'], ebit)
        self.assertEqual(ebit, Decimal('-330.00'))
        # risultati pieni: self -240, servito -90, prodotti 0; il Comune va a zero
        self.assertEqual(a['quadro'][-1]['nome'], 'Risultato pieno')
        self.assertEqual(a['quadro'][-1]['valori'], [Decimal('-240.00'), Decimal('-90.00'), ZERO, ZERO])
        self.assertEqual(a['self']['risultato'], Decimal('-240.00'))

    def test_rientro_investimenti(self):
        from .models import ContoFinanziario, MovimentoFinanziario
        MovimentoFinanziario.objects.create(conto=ContoFinanziario.objects.get(nome='BAPS'), data=date(2026, 10, 15),
                                            importo=Decimal('-50'), voce=VoceConto.objects.get(codice='N.FINANZIAMENTI'),
                                            impronta='rata')
        r = analitica([OTTOBRE], 'scontrini')['rientro']
        self_ = next(x for x in r['righe'] if x['nome'] == 'Self-service')
        # self: ricavi 200 - costi 240 + ammortamento 100 = 60 al mese -> 720 all'anno su 1200 investiti
        self.assertEqual((self_['investito'], self_['ante'], self_['anni_ante']), (Decimal('1200'), Decimal('720'), Decimal('1.7')))
        self.assertEqual(self_['rientro_ante'].year, 2028)
        # rata 50 al mese -> 600 all'anno, tutta sul self (unico centro con cespiti)
        self.assertEqual((self_['dopo'], self_['anni_dopo']), (Decimal('120'), Decimal('10.0')))
        # in totale l'EBITDA (-230 al mese) e' negativo: non rientra
        self.assertEqual((r['totale']['ante'], r['totale']['anni_ante']), (Decimal('-2760'), None))
        Cespite.objects.all().delete()
        self.assertIsNone(analitica([OTTOBRE], 'scontrini')['rientro'])

    def test_ricavi_dal_rilevato(self):
        from apps.finanze.models import QuadraturaGiornaliera
        # rilevato 366: il servito e' il totale ordini (122) meno l'IVA del registratore (22);
        # i portali sono il resto (366 - 122 = 244) meno l'IVA dei corrispettivi del portale (44)
        QuadraturaGiornaliera.objects.create(data=date(2026, 10, 6), contanti_totali=Decimal('366'))
        a = analitica([OTTOBRE], 'rilevato')
        self.assertEqual(a['servito']['ricavi'], Decimal('100.00'))
        self.assertEqual(a['self']['ricavi'], Decimal('200.00'))
        self.assertEqual(a['self']['ricavo_ciclo'], Decimal('10'))
        self.assertEqual(a['quadro'][0]['totale'], Decimal('300.00'))
        ebit = next(r['totale'] for r in conto_economico([OTTOBRE], 'rilevato')['righe'] if r['chiave'] == 'ebit')
        self.assertEqual(a['quadro'][-1]['totale'], ebit)
        self.assertEqual(analitica([OTTOBRE], 'scontrini')['self']['ricavi'], Decimal('200.00'))   # scontrini: invariato


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
                    '/contabilita/analitica/?anno=2026&ricavi=xyz',
                    '/contabilita/analitica/?anno=2026&ricavi=scontrini',
                    '/contabilita/analitica/impostazioni/'):
            self.assertEqual(self.apri(url).status_code, 200, url)
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'senza ripartizione')
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'Nessun cespite registrato')
        Cespite.objects.create(descrizione='Portale', data_acquisto=date(2026, 1, 10), valore=Decimal('1200'), anni=5, centro='self')
        self.assertContains(self.apri('/contabilita/analitica/?anno=2026'), 'acquisto medio 01/2026')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'personale', 'dal': '2026-10',
                                                                  f'rip_{d.pk}_servito': '80', f'rip_{d.pk}_self': '20'})
        r = RipartizionePersonale.objects.get()
        self.assertEqual((r.valido_dal, r.ripartizione), (OTTOBRE, {'servito': 80, 'self': 20}))
        affitto = VoceConto.objects.get(codice='CF.AFFITTO')
        self.assertContains(self.apri('/contabilita/analitica/impostazioni/'), f'drv_v_{affitto.pk}')
        self.client.post('/contabilita/analitica/impostazioni/', {'azione': 'driver', f'drv_v_{affitto.pk}': 'cicli',
                                                                  f'drv_c_{Cespite.objects.get().pk}': 'ore'})
        affitto.refresh_from_db()
        self.assertEqual(affitto.driver_comuni, 'cicli')
        self.assertEqual(Cespite.objects.get().driver_comuni, 'ricavi')      # cespite del self: non si tocca
