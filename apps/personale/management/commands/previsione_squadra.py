"""Calcola e salva la squadra consigliata per domani. Da lanciare ogni
sera dopo la fine del turno (19): la pagina Previsione squadra la salva
comunque quando viene aperta dopo quell'ora. Prima delle 19 non salva,
cosi' il cron puo' girare a due ore UTC e coprire ora legale e solare."""
from django.core.management.base import BaseCommand

from apps.personale import previsione


class Command(BaseCommand):
    help = 'Previsione dei lavaggi e squadra consigliata per domani'

    def handle(self, *args, **opzioni):
        r = previsione.calcola()
        if r.get('errore'):
            self.stderr.write(r['errore'])
            return
        salvata = previsione.registra(r)
        domani = next(p for p in r['previsioni'] if p['domani'])
        self.stdout.write(f"{domani['data']:%d/%m/%Y}: {domani['previsti']:.0f} lavaggi previsti, "
                          f"squadra {domani['squadra'] or 'chiuso'}{'' if salvata else ' (non salvata: turno non finito)'}")
