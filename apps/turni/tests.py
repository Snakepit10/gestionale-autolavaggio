from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cq.models import PostazioneCQ
from apps.ordini.models import Ordine
from apps.turni import report as rep
from apps.turni.models import LavorazioneOperatore, SessioneTurno
from apps.turni.templatetags.report_turni import durata


class PeriodoTest(TestCase):
    OGGI = date(2026, 3, 15)

    def test_predefiniti(self):
        self.assertEqual(rep.periodo({}, self.OGGI), ('mese', date(2026, 3, 1), self.OGGI))
        self.assertEqual(rep.periodo({'periodo': '7g'}, self.OGGI), ('7g', date(2026, 3, 9), self.OGGI))
        self.assertEqual(rep.periodo({'periodo': 'mese_scorso'}, self.OGGI),
                         ('mese_scorso', date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(rep.periodo({'periodo': 'trimestre'}, self.OGGI)[0], '90g')
        self.assertEqual(rep.periodo({'periodo': 'boh'}, self.OGGI)[0], 'mese')

    def test_date_libere(self):
        self.assertEqual(rep.periodo({'dal': '2026-01-10', 'al': '2026-01-05'}, self.OGGI),
                         ('personalizzato', date(2026, 1, 5), date(2026, 1, 10)))
        self.assertEqual(rep.periodo({'dal': '2026-03-01', 'al': '2026-03-15'}, self.OGGI)[0], 'mese')
        self.assertEqual(rep.periodo({'dal': 'x', 'al': ''}, self.OGGI)[0], 'mese')

    def test_durata(self):
        self.assertEqual(durata(4.5), '4,5 min')
        self.assertEqual(durata(23.4), '23 min')
        self.assertEqual(durata(125), '2 h 05')
        self.assertEqual(durata(None), '—')


class ReportTest(TestCase):
    def setUp(self):
        self.utente = User.objects.create_superuser('capo_test', 'capo@example.com', 'x')
        self.mario = User.objects.create_user('mario_test', first_name='Mario', last_name='Prova')
        self.sessione = SessioneTurno.objects.create(operatore=self.mario)
        self.prelavaggio = PostazioneCQ.objects.create(codice='t-pre', nome='Prelavaggio prova', sigla='PP', ordine=1)
        self.asciugatura = PostazioneCQ.objects.create(codice='t-asc', nome='Asciugatura prova', sigla='AP', ordine=2)
        self.inizio = timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())) + timedelta(hours=9)

    def _ordine(self, *fasi):
        ordine = Ordine.objects.create(totale=Decimal('20'), totale_finale=Decimal('20'))
        t = self.inizio
        for postazione, minuti in fasi:
            LavorazioneOperatore.objects.create(
                sessione=self.sessione, ordine=ordine, postazione_cq=postazione,
                inizio=t, fine=t + timedelta(minutes=minuti), stato='completato')
            t += timedelta(minutes=minuti)
        return ordine

    def test_report_e_fuori_scala(self):
        for _ in range(5):
            self._ordine((self.prelavaggio, 10), (self.asciugatura, 6))
        lungo = self._ordine((self.prelavaggio, 45), (self.asciugatura, 6))
        oggi = timezone.localdate()
        d = rep.report(oggi, oggi)
        self.assertEqual(d['n_ordini'], 6)
        self.assertEqual(d['n_fasi'], 12)
        self.assertEqual(d['mediana_ordine'], 16)
        self.assertEqual(d['fuori_scala'], 1)
        pre = d['postazioni'][0]
        self.assertEqual((pre['nome'], pre['mediana'], pre['fuori_scala'], pre['barra']), ('Prelavaggio prova', 10, 1, 100))
        self.assertEqual(d['operatori'][0]['nome'], 'Mario Prova')
        self.assertEqual(d['operatori'][0]['ordini'], 6)
        self.assertTrue(next(o for o in d['ordini'] if o['ordine'] == lungo)['fuori_scala'])
        self.assertEqual(rep.report(oggi, oggi, postazione_id=self.asciugatura.pk)['n_fasi'], 6)

    def apri(self, url):
        # La vista chiamata direttamente: il client di test non copia il
        # contesto dei template con Python 3.14.
        req = RequestFactory().get(url)
        req.user, req.session = self.utente, {}
        req._messages = FallbackStorage(req)
        trovata = resolve(url.split('?')[0])
        risposta = trovata.func(req, *trovata.args, **trovata.kwargs)
        self.assertEqual(risposta.status_code, 200)
        return risposta.content.decode()

    def test_pagine(self):
        ordine = self._ordine((self.prelavaggio, 10), (self.asciugatura, 6))
        report = reverse('turni:report_lavorazioni')
        html = self.apri(report + '?periodo=7g')
        self.assertIn('Prelavaggio prova', html)
        self.assertIn(ordine.numero_progressivo, html)
        self.assertIn('Nessuna fase rilevata', self.apri(report + '?dal=2000-01-01&al=2000-01-02'))
        html = self.apri(reverse('turni:report_ordine_dettaglio', args=[ordine.pk]) + '?periodo=7g')
        self.assertIn('periodo=7g', html)
        self.assertIn('16 min', html)
        d = rep.dettaglio_ordine(ordine)
        self.assertEqual([f['sinistra'] for f in d['fasi']], [0, 62.5])
