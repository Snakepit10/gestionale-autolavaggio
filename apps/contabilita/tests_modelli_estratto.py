"""Test dei modelli di estratto conto: riconoscimento degli schemi senza
intestazione (MPS, BAPS xlsx, BAPS xls), totale e saldi di controllo,
modelli personalizzati, anteprima che non importa. Dati inventati."""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .estratti import (ErroreEstratto, anteprima, colonna, importa_estratto, leggi_righe, lettera,
                       modello_da_chiave)
from .models import ContoFinanziario, ModelloEstratto, MovimentoFinanziario
from .tests_prima_nota import xlsx

# Stessa forma dei file delle banche, valori inventati
MPS = [
    [datetime(2026, 2, 20), datetime(2026, 2, 20), 'Incasso tramite POS', 'Accredito terminale prova', ' ', 40, None, None],
    [datetime(2026, 2, 20), datetime(2026, 2, 20), 'Incasso tramite POS', 'Accredito terminale prova', ' ', 40, None, None],
    [datetime(2026, 2, 19), datetime(2026, 2, 1), 'Canone mensile', 'Canone conto di prova', ' ', -7, None, None],
    [datetime(2026, 2, 18), datetime(2026, 2, 18), 'Bonifico', 'Bonifico a Ditta Prova', ' ', -20.5, None, None],
    [None, None, None, 'Totale (EUR)', None, 52.5, None, None],
]
BAPS_XLSX = [
    ['02/02/2026', '01/02/2026', 'EUR', '-1,25', 'PAGAMENTO POS\nNegozio di prova', '998,75'],
    ['03/02/2026', '03/02/2026', 'EUR', '-48,75', 'ADDEBITO SDD\nUtenza di prova', '950,00'],
    ['04/02/2026', '04/02/2026', 'EUR', '150,00', 'BONIFICO A VOSTRO FAVORE\nCliente prova', '1.100,00'],
]
BAPS_XLS = [
    ['01/04/2026', '01/04/2026', None, 300.0, '"Bonifico da cliente di prova', '"-"'],
    ['02/04/2026', '01/04/2026', None, -12.5, 'Pagamento POS negozio di prova', '"-"'],
    ['03/04/2026', '03/04/2026', None, -2.0, '"Commissioni bonifico', '"-"'],
]


class RiconoscimentoTest(TestCase):
    def test_mps_senza_intestazione_col_totale(self):
        lettura = leggi_righe(MPS)
        self.assertEqual(lettura.modello.chiave, 'mps')
        self.assertEqual(len(lettura.movimenti), 4)
        m = lettura.movimenti[2]
        self.assertEqual((m['data'], m['data_valuta'], m['causale'], m['importo']),
                         (date(2026, 2, 19), date(2026, 2, 1), 'Canone mensile', Decimal('-7.00')))
        self.assertEqual(lettura.totale_file, Decimal('52.50'))
        self.assertTrue(lettura.quadra_col_totale)
        self.assertIsNone(lettura.saldo_finale)                    # "Totale" non e' un saldo

    def test_baps_xlsx_con_saldi(self):
        lettura = leggi_righe(BAPS_XLSX)
        self.assertEqual(lettura.modello.chiave, 'baps_xlsx')
        self.assertEqual([m['saldo'] for m in lettura.movimenti], [Decimal('998.75'), Decimal('950.00'), Decimal('1100.00')])
        self.assertTrue(lettura.saldi_coerenti)
        self.assertEqual(lettura.movimenti[1]['descrizione'], 'ADDEBITO SDD Utenza di prova')

    def test_baps_xls_senza_intestazione(self):
        lettura = leggi_righe(BAPS_XLS)
        self.assertEqual(lettura.modello.chiave, 'baps_xls')
        self.assertEqual([m['importo'] for m in lettura.movimenti], [Decimal('300.00'), Decimal('-12.50'), Decimal('-2.00')])
        self.assertEqual(lettura.movimenti[0]['descrizione'], 'Bonifico da cliente di prova')   # virgolette tolte
        self.assertIsNone(lettura.movimenti[0]['saldo'])                                       # la nota non e' un saldo

    def test_saldo_finale_senza_data(self):
        righe = MPS[:-1] + [[None, None, None, 'Saldo finale', None, 1234.5, None, None]]
        self.assertEqual(leggi_righe(righe).saldo_finale, Decimal('1234.50'))

    def test_saldi_incoerenti_e_totale_sbagliato(self):
        righe = [r[:] for r in BAPS_XLSX]
        righe[1][5] = '900,00'
        self.assertFalse(leggi_righe(righe).saldi_coerenti)
        righe = MPS[:-1] + [[None, None, None, 'Totale', None, 99, None, None]]
        self.assertFalse(leggi_righe(righe).quadra_col_totale)

    def test_niente_di_riconoscibile(self):
        with self.assertRaises(ErroreEstratto):
            leggi_righe([['a', 'b', 'c'], ['1', '2', '3']])

    def test_modello_forzato(self):
        # letto col modello MPS un estratto BAPS prende il saldo come importo: per questo
        # il riconoscimento controlla la forma delle righe; forzato, si usa comunque
        lettura = leggi_righe(BAPS_XLS, modello_da_chiave('baps_xls'))
        self.assertEqual(len(lettura.movimenti), 3)


class ModelloPersonalizzatoTest(TestCase):
    def test_colonne(self):
        self.assertEqual((colonna('A'), colonna('c'), colonna('7'), colonna(''), colonna('AB')), (1, 3, 7, None, 28))
        self.assertEqual((lettera(1), lettera(28)), ('A', 'AB'))
        with self.assertRaises(ValueError):
            colonna('A1')

    def test_dare_avere_in_ordine_diverso(self):
        m = ModelloEstratto.objects.create(nome='Banca prova', col_data=4, col_dare=2, col_avere=3, col_descrizione=1)
        righe = [['Elenco movimenti', None, None, None],
                 ['Pagamento fornitore prova', '120,00', '', '05/03/2026'],
                 ['Accredito cliente prova', '', '80,00', '06/03/2026'],
                 ['Totale', '120,00', '80,00', None]]
        modello = modello_da_chiave(f'pers_{m.pk}')
        lettura = leggi_righe(righe, modello)
        self.assertEqual([(x['data'], x['importo']) for x in lettura.movimenti],
                         [(date(2026, 3, 5), Decimal('-120.00')), (date(2026, 3, 6), Decimal('80.00'))])

    def test_modello_del_conto(self):
        conto = ContoFinanziario.objects.get(nome='BAPS')
        m = ModelloEstratto.objects.create(nome='Inverso', col_data=2, col_importo=1, col_descrizione=3)
        conto.modello_estratto = f'pers_{m.pk}'
        conto.save()
        dati = xlsx([[-5, '07/03/2026', 'Spesa prova'], [9, '08/03/2026', 'Entrata prova']])
        esito = importa_estratto(conto, 'x.xlsx', dati)
        self.assertEqual((esito.nuovi, esito.modello), (2, 'Inverso'))


class AnteprimaTest(TestCase):
    def test_non_importa(self):
        prova = anteprima('mps.xlsx', xlsx(MPS))
        self.assertEqual((prova['modello'].chiave, prova['n'], prova['quadra_col_totale']), ('mps', 4, True))
        self.assertEqual((prova['dal'], prova['al']), (date(2026, 2, 18), date(2026, 2, 20)))
        self.assertEqual(prova['entrate'], Decimal('80.00'))
        self.assertFalse(MovimentoFinanziario.objects.exists())

    def test_saldo_finale_sull_ultimo_giorno(self):
        conto = ContoFinanziario.objects.get(nome='MPS')
        righe = MPS[:-1] + [[None, None, None, 'Saldo contabile', None, 52.5, None, None]]
        importa_estratto(conto, 'mps.xlsx', xlsx(righe))
        self.assertEqual(set(conto.movimenti.filter(saldo_estratto__isnull=False).values_list('data', 'saldo_estratto')),
                         {(date(2026, 2, 20), Decimal('52.50'))})
        self.assertEqual(conto.saldo_al(date(2026, 2, 20)), Decimal('52.50'))


class VisteModelliTest(TestCase):
    def setUp(self):
        from apps.auth_system.models import PermessoGruppo
        self.titolare = User.objects.create_user('titolare1', password='x')
        gruppo = Group.objects.get_or_create(name='titolare')[0]
        self.titolare.groups.add(gruppo)
        PermessoGruppo.objects.get_or_create(sezione='contabilita', gruppo=gruppo)
        self.client.force_login(self.titolare)

    def _richiesta(self, metodo, dati=None):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from .views_prima_nota import modelli_estratto
        req = getattr(RequestFactory(), metodo)('/contabilita/modelli-estratto/', dati or {})
        req.user, req.session = self.titolare, {}
        req._messages = FallbackStorage(req)
        return modelli_estratto(req)

    def test_pagina_e_anteprima(self):
        self.assertContains(self._richiesta('get'), 'BAPS - Excel 97-2003')
        risposta = self._richiesta('post', {'azione': 'anteprima', 'modello': '',
                                            'file': SimpleUploadedFile('baps.xlsx', xlsx(BAPS_XLSX))})
        self.assertContains(risposta, 'saldi riga per riga coerenti')
        self.assertContains(risposta, 'BAPS - Excel senza intestazione, con saldo')
        self.assertFalse(MovimentoFinanziario.objects.exists())

    def test_salva_modello_e_conto(self):
        self.client.post('/contabilita/modelli-estratto/', {'azione': 'salva', 'nome': 'Banca prova', 'col_data': 'b',
                                                            'col_importo': 'E', 'col_descrizione': '3'})
        m = ModelloEstratto.objects.get()
        self.assertEqual((m.col_data, m.col_importo, m.col_descrizione), (2, 5, 3))
        self.client.post('/contabilita/modelli-estratto/', {'azione': 'salva', 'nome': 'Doppia', 'col_data': 'A',
                                                            'col_importo': 'A'})
        self.assertFalse(ModelloEstratto.objects.filter(nome='Doppia').exists())     # stessa colonna due volte
        conto = ContoFinanziario.objects.get(nome='BAPS')
        self.client.post('/contabilita/conti/', {'azione': 'salva_conto', 'id': conto.pk, 'nome': 'BAPS', 'tipo': 'banca',
                                                 'data_saldo_iniziale': '2026-01-01', 'attivo': '1',
                                                 'modello_estratto': 'baps_xls'})
        conto.refresh_from_db()
        self.assertEqual(conto.modello_estratto, 'baps_xls')
