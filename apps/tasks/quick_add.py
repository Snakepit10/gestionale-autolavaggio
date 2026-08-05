"""Parser dell'Aggiunta Rapida stile Todoist (linguaggio naturale, IT).

Riconosce dentro il testo della task:
- #progetto      -> progetto esistente (match sul nome, case-insensitive)
- @etichetta     -> etichetta (creata al volo se non esiste)
- +persona       -> assegnatario tra gli operatori (username/nome/cognome)
- p1..p4         -> priorita'
- date/orari in italiano: "oggi", "domani", "dopodomani", "stasera",
  giorni della settimana ("venerdi"), "3 marzo [2027]", "15/08[/2026]",
  orari "alle 16", "alle 16:30", "ore 18".

I token riconosciuti vengono rimossi dal titolo. Tutto il resto resta
testo. Niente ricorrenze ("ogni martedi"): il modello non le supporta.
Data senza orario -> ore 18:00; solo orario -> oggi (o domani se
l'orario e' gia' passato).
"""
import re
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

ORA_DEFAULT = 18  # scadenza a fine giornata lavorativa se manca l'orario

_GIORNI = {
    'lunedi': 0, 'martedi': 1, 'mercoledi': 2, 'giovedi': 3,
    'venerdi': 4, 'sabato': 5, 'domenica': 6,
}
_MESI = {
    'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4, 'maggio': 5,
    'giugno': 6, 'luglio': 7, 'agosto': 8, 'settembre': 9,
    'ottobre': 10, 'novembre': 11, 'dicembre': 12,
}


def _senza_accenti(s: str) -> str:
    return (s.replace('à', 'a').replace('è', 'e').replace('é', 'e')
             .replace('ì', 'i').replace('ò', 'o').replace('ù', 'u'))


def parse_quick(testo: str, user):
    """Analizza il testo del quick-add. Ritorna un dict:
    {titolo, progetto, etichette, assegnatari, priorita, scadenza,
     riconosciuti} dove riconosciuti e' la lista leggibile di cio' che
    e' stato estratto (per il messaggio di conferma).
    """
    from apps.cq.forms import get_operatori_queryset

    from .models import Etichetta, Progetto, Task

    out = {
        'titolo': testo, 'progetto': None, 'etichette': [],
        'assegnatari': [], 'priorita': Task.P4, 'scadenza': None,
        'riconosciuti': [],
    }
    resto = testo

    # --- #progetto (solo esistenti: un refuso non deve creare progetti)
    def _sub_progetto(m):
        nome = m.group(1)
        p = (Progetto.objects.filter(archiviato=False)
             .filter(nome__iexact=nome).first()
             or Progetto.objects.filter(archiviato=False)
             .filter(nome__icontains=nome).first())
        if p is not None:
            out['progetto'] = p
            out['riconosciuti'].append(f'progetto {p.nome}')
            return ' '
        return m.group(0)  # non trovato: resta testo
    resto = re.sub(r'#([\w\-]+)', _sub_progetto, resto)

    # --- @etichetta (creata al volo se manca, come Todoist)
    def _sub_etichetta(m):
        nome = m.group(1)
        e = Etichetta.objects.filter(nome__iexact=nome).first()
        if e is None:
            e = Etichetta.objects.create(nome=nome[:50])
        out['etichette'].append(e)
        out['riconosciuti'].append(f'etichetta {e.nome}')
        return ' '
    resto = re.sub(r'@([\w\-]+)', _sub_etichetta, resto)

    # --- +persona (match tra gli operatori)
    operatori = get_operatori_queryset()

    def _sub_persona(m):
        nome = m.group(1)
        u = operatori.filter(
            Q(username__iexact=nome) | Q(first_name__iexact=nome)
            | Q(last_name__iexact=nome)
        ).first() or operatori.filter(
            Q(username__icontains=nome) | Q(first_name__icontains=nome)
            | Q(last_name__icontains=nome)
        ).first()
        if u is not None:
            out['assegnatari'].append(u)
            out['riconosciuti'].append(
                f'assegnata a {u.get_full_name() or u.username}')
            return ' '
        return m.group(0)
    resto = re.sub(r'\+([\w\.\-]+)', _sub_persona, resto)

    # --- priorita' p1..p4
    def _sub_prio(m):
        out['priorita'] = int(m.group(1))
        out['riconosciuti'].append(f'priorita P{m.group(1)}')
        return ' '
    resto = re.sub(r'(?<![\w])[pP]([1-4])(?![\w])', _sub_prio, resto, count=1)

    # --- data e orario -------------------------------------------------
    ora_locale = timezone.localtime(timezone.now())
    data_trovata = None   # date
    ora_trovata = None    # (ora, minuti)
    resto_na = _senza_accenti(resto.lower())

    def _rimuovi(pattern):
        """Rimuove dal testo originale il pezzo che matcha (case/accents
        insensitive), preservando il resto."""
        nonlocal resto, resto_na
        m = re.search(pattern, resto_na)
        if m:
            resto = resto[:m.start()] + ' ' + resto[m.end():]
            resto_na = resto_na[:m.start()] + ' ' + resto_na[m.end():]
        return m

    # orario: "alle 16", "alle 16:30", "ore 18", "ore 18.30"
    m = _rimuovi(r'\b(?:alle?|ore)\s+(\d{1,2})(?:[:\.](\d{2}))?\b')
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            ora_trovata = (h, int(m.group(2) or 0))

    # parole chiave
    m = _rimuovi(r'\b(oggi|domani|dopodomani|stasera)\b')
    if m:
        parola = m.group(1)
        if parola == 'domani':
            data_trovata = (ora_locale + timedelta(days=1)).date()
        elif parola == 'dopodomani':
            data_trovata = (ora_locale + timedelta(days=2)).date()
        else:
            data_trovata = ora_locale.date()
            if parola == 'stasera' and ora_trovata is None:
                ora_trovata = (20, 0)

    # giorno della settimana: prossima occorrenza (oggi incluso)
    if data_trovata is None:
        m = _rimuovi(r'\b(lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)\b')
        if m:
            delta = (_GIORNI[m.group(1)] - ora_locale.weekday()) % 7
            data_trovata = (ora_locale + timedelta(days=delta)).date()

    # "3 marzo" / "3 marzo 2027"
    if data_trovata is None:
        m = _rimuovi(r'\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|'
                     r'luglio|agosto|settembre|ottobre|novembre|dicembre)'
                     r'(?:\s+(\d{4}))?\b')
        if m:
            giorno, mese = int(m.group(1)), _MESI[m.group(2)]
            anno = int(m.group(3)) if m.group(3) else ora_locale.year
            try:
                d = datetime(anno, mese, giorno).date()
                # senza anno esplicito, una data gia' passata slitta al
                # prossimo anno (come Todoist)
                if not m.group(3) and d < ora_locale.date():
                    d = datetime(anno + 1, mese, giorno).date()
                data_trovata = d
            except ValueError:
                pass

    # "15/08" / "15/08/2026" (anche con -)
    if data_trovata is None:
        m = _rimuovi(r'\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b')
        if m:
            giorno, mese = int(m.group(1)), int(m.group(2))
            anno = m.group(3)
            anno = (int(anno) + 2000 if anno and len(anno) == 2
                    else int(anno) if anno else ora_locale.year)
            try:
                d = datetime(anno, mese, giorno).date()
                if not m.group(3) and d < ora_locale.date():
                    d = datetime(anno + 1, mese, giorno).date()
                data_trovata = d
            except ValueError:
                pass

    if data_trovata is not None or ora_trovata is not None:
        if data_trovata is None:
            # solo orario: oggi, o domani se l'orario e' gia' passato
            data_trovata = ora_locale.date()
            if (ora_trovata[0], ora_trovata[1]) <= (ora_locale.hour, ora_locale.minute):
                data_trovata = data_trovata + timedelta(days=1)
        h, mn = ora_trovata if ora_trovata is not None else (ORA_DEFAULT, 0)
        out['scadenza'] = timezone.make_aware(
            datetime(data_trovata.year, data_trovata.month, data_trovata.day, h, mn))
        out['riconosciuti'].append(
            'scadenza ' + out['scadenza'].strftime('%d/%m %H:%M'))

    # --- titolo finale: testo residuo ripulito
    titolo = re.sub(r'\s{2,}', ' ', resto).strip(' ,;-')
    out['titolo'] = titolo or testo.strip()
    return out
