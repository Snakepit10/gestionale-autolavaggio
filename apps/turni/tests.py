from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cq.models import BloccoPostazione, PostazioneCQ
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

    def test_blocchi_della_stessa_fase_contano_una_volta(self):
        # "Avvia" crea una riga per blocco con gli stessi orari: e' una fase sola.
        interni = BloccoPostazione.objects.create(postazione=self.asciugatura, codice='t-int', nome='Interni')
        bauliera = BloccoPostazione.objects.create(postazione=self.asciugatura, codice='t-bau', nome='Bauliera')
        ordine = self._ordine((self.prelavaggio, 10))
        t = self.inizio + timedelta(minutes=10)
        for blocco, secondi in ((interni, 0), (bauliera, 2)):
            LavorazioneOperatore.objects.create(
                sessione=self.sessione, ordine=ordine, postazione_cq=self.asciugatura, blocco=blocco,
                inizio=t, fine=t + timedelta(minutes=6, seconds=secondi), stato='completato')
        # La stessa postazione rifatta piu' tardi resta una fase a parte.
        LavorazioneOperatore.objects.create(
            sessione=self.sessione, ordine=ordine, postazione_cq=self.asciugatura, blocco=interni,
            inizio=t + timedelta(minutes=30), fine=t + timedelta(minutes=33), stato='completato')
        oggi = timezone.localdate()
        d = rep.report(oggi, oggi)
        self.assertEqual(d['n_fasi'], 3)
        self.assertAlmostEqual(d['ordini'][0]['minuti'], 10 + 6 + 2 / 60 + 3)
        dettaglio = rep.dettaglio_ordine(ordine)
        self.assertEqual([f['blocco'] for f in dettaglio['fasi']], ['', 'Interni, Bauliera', 'Interni'])

    def test_fase_ripresa_entro_due_minuti(self):
        ordine = self._ordine((self.asciugatura, 6))
        t = self.inizio + timedelta(minutes=6, seconds=30)
        # Dati gia' registrati: chiusa e riavviata dopo 30 secondi, e poi
        # ancora dopo 5 minuti (questa resta separata).
        for da, a in ((0, 6), (6.5, 17.5), (22.5, 25.5)):
            LavorazioneOperatore.objects.create(
                sessione=self.sessione, ordine=ordine, postazione_cq=self.prelavaggio,
                inizio=t + timedelta(minutes=da), fine=t + timedelta(minutes=a), stato='completato')
        fasi = rep.dettaglio_ordine(ordine)['fasi']
        # asciugatura 6; prelavaggio 6 + 11 ripresa dopo 30 secondi; prelavaggio 3
        self.assertEqual([round(f['minuti'], 1) for f in fasi], [6, 17, 3])
        self.assertEqual([f['riprese'] for f in fasi], [0, 1, 0])
        self.assertAlmostEqual(fasi[1]['pausa'], 0.5)

    def test_riavviare_una_postazione_chiusa_la_riapre(self):
        from apps.turni.models import PostazioneTurno
        from apps.turni.views import api_inizia_lavoro
        SessioneTurno.objects.filter(pk=self.sessione.pk).update(stato='attivo')
        PostazioneTurno.objects.create(sessione=self.sessione, postazione_cq=self.prelavaggio)
        ordine = Ordine.objects.create(totale=Decimal('20'), totale_finale=Decimal('20'))
        chiusa = LavorazioneOperatore.objects.create(
            sessione=self.sessione, ordine=ordine, postazione_cq=self.prelavaggio,
            inizio=timezone.now() - timedelta(minutes=10), stato='completato',
            fine=timezone.now() - timedelta(minutes=1))
        req = RequestFactory().post('/')
        req.user = self.mario
        self.assertEqual(api_inizia_lavoro(req, ordine.pk).status_code, 200)
        self.assertEqual(LavorazioneOperatore.objects.filter(ordine=ordine).count(), 1)
        chiusa.refresh_from_db()
        self.assertEqual((chiusa.stato, chiusa.fine), ('in_lavorazione', None))
        self.assertAlmostEqual(chiusa.tempo_pausa_totale.total_seconds(), 60, delta=5)
        self.assertAlmostEqual(chiusa.tempo_lavoro_netto_minuti, 9, delta=0.1)

    def test_elimina_tempi(self):
        from apps.turni.views import report_elimina_tempi
        prova = self._ordine((self.prelavaggio, 10), (self.asciugatura, 6))
        vero = self._ordine((self.prelavaggio, 8), (self.asciugatura, 5))

        def invia(dati):
            req = RequestFactory().post('/turni/report/elimina/', dati)
            req.user, req.session = self.utente, {}
            req._messages = FallbackStorage(req)
            return report_elimina_tempi(req)

        r = invia({'ordine': [prova.pk], 'torna': '/turni/report/?periodo=7g'})
        self.assertEqual((r.status_code, r.url), (302, '/turni/report/?periodo=7g'))
        self.assertFalse(LavorazioneOperatore.objects.filter(ordine=prova).exists())
        self.assertTrue(Ordine.objects.filter(pk=prova.pk).exists())
        self.assertEqual(LavorazioneOperatore.objects.filter(ordine=vero).count(), 2)

        fase = rep.dettaglio_ordine(vero)['fasi'][0]
        r = invia({'lavorazione': fase['ids'], 'torna': 'https://altro-sito.example/'})
        self.assertEqual(r.url, reverse('turni:report_lavorazioni'))
        self.assertEqual(LavorazioneOperatore.objects.filter(ordine=vero).count(), 1)

        html = self.apri(reverse('turni:report_ordine_dettaglio', args=[vero.pk]))
        self.assertIn("Elimina i tempi di quest'ordine", html)
