"""
Logica di calcolo per il sistema premi e sanzioni CQ.
Implementa le regole della sezione 7.2 del manuale operativo.
"""
from decimal import Decimal


# ---------------------------------------------------------------------------
# Mappa zone → responsabilità postazioni
# (da sezione 6 del documento "sezione_72_premi_sanzioni_v3.docx")
#
# 'produttore': postazione che genera il difetto (punteggio pieno)
# 'catena': postazioni intermedie che avrebbero dovuto intercettare (50%)
# NB: 'controllo_finale' è SEMPRE aggiunto dinamicamente come catena
# ---------------------------------------------------------------------------
ZONE_RESPONSABILITA = {
    # Esterno
    'carrozzeria_aloni_residui': {'produttore': 'post1', 'catena': ['post2']},
    'parabrezza_esterno':        {'produttore': 'post1', 'catena': ['post2', 'post4']},
    'lunotto_esterno':           {'produttore': 'post1', 'catena': ['post2', 'post4']},
    'vetri_laterali_esterni':    {'produttore': 'post1', 'catena': ['post2', 'post4']},
    'specchietti_esterni':       {'produttore': 'post1', 'catena': ['post2', 'post4']},
    'cerchi':                    {'produttore': 'post1', 'catena': []},
    'passaruota':                {'produttore': 'post1', 'catena': []},
    'gomme_nero_gomme':          {'produttore': 'post3', 'catena': []},
    'paraurti':                  {'produttore': 'post1', 'catena': ['post2']},
    # Interni — plastiche (produttore post4)
    'cruscotto_plancia':         {'produttore': 'post4', 'catena': []},
    'bocchette_aria':            {'produttore': 'post4', 'catena': []},
    'tunnel_centrale':           {'produttore': 'post4', 'catena': []},
    'montanti':                  {'produttore': 'post4', 'catena': []},
    'battitacchi':               {'produttore': 'post4', 'catena': []},
    'sedile_guidatore':          {'produttore': 'post4', 'catena': []},
    'sedile_passeggero':         {'produttore': 'post4', 'catena': []},
    'sedili_posteriori':         {'produttore': 'post4', 'catena': []},
    'vani_portiere':             {'produttore': 'post4', 'catena': []},
    'pannelli_portiere':         {'produttore': 'post4', 'catena': []},
    # Interni — moquette e tappeti (produttore post3)
    'moquette_ant_sx':           {'produttore': 'post3', 'catena': []},
    'moquette_ant_dx':           {'produttore': 'post3', 'catena': []},
    'moquette_post_sx':          {'produttore': 'post3', 'catena': []},
    'moquette_post_dx':          {'produttore': 'post3', 'catena': []},
    'tappeto_guidatore':         {'produttore': 'post3', 'catena': []},
    'tappeto_passeggero':        {'produttore': 'post3', 'catena': []},
    # Vetri interni (produttore post4)
    'parabrezza_interno':        {'produttore': 'post4', 'catena': []},
    'lunotto_interno':           {'produttore': 'post4', 'catena': []},
    'vetri_laterali_interni':    {'produttore': 'post4', 'catena': []},
    'specchietto_retrovisore_int': {'produttore': 'post4', 'catena': []},
    'parasoli':                  {'produttore': 'post4', 'catena': []},
    # Vani
    'cofano_interno':            {'produttore': 'post3', 'catena': []},
    'bagagliaio':                {'produttore': 'post3', 'catena': []},
    'cassetto_guanti':           {'produttore': 'post4', 'catena': []},
}

# ---------------------------------------------------------------------------
# Difetti possibili per zona (usato nel form CQ tablet-friendly)
# ---------------------------------------------------------------------------
DIFETTI_PER_ZONA = {
    # Esterno
    'carrozzeria_aloni_residui': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'parabrezza_esterno': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'lunotto_esterno': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'vetri_laterali_esterni': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'specchietti_esterni': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'cerchi': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    'passaruota': [
        'briciole_sabbia_polvere', 'fango', 'zona_non_trattata', 'altro',
    ],
    'gomme_nero_gomme': [
        'nero_gomme_non_uniforme', 'nero_gomme_mancante', 'zona_non_trattata', 'altro',
    ],
    'paraurti': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica', 'escrementi_insetti',
        'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_carrozzeria', 'altro',
    ],
    # Interni — plastiche
    'cruscotto_plancia': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    'bocchette_aria': [
        'briciole_sabbia_polvere', 'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro',
    ],
    'tunnel_centrale': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    'montanti': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    'battitacchi': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    'sedile_guidatore': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata',
        'foglio_protettivo_mancante', 'profumo_non_applicato', 'graffio_plastica', 'altro',
    ],
    'sedile_passeggero': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata',
        'foglio_protettivo_mancante', 'graffio_plastica', 'altro',
    ],
    'sedili_posteriori': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata',
        'foglio_protettivo_mancante', 'graffio_plastica', 'altro',
    ],
    'vani_portiere': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    'pannelli_portiere': [
        'briciole_sabbia_polvere', 'fango', 'macchia_organica',
        'alone_plastica', 'prodotto_non_rimosso', 'zona_non_trattata', 'graffio_plastica', 'altro',
    ],
    # Interni — moquette
    'moquette_ant_sx':   ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'moquette_ant_dx':   ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'moquette_post_sx':  ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'moquette_post_dx':  ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'tappeto_guidatore': ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'tappeto_passeggero':['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    # Vetri interni
    'parabrezza_interno':         ['briciole_sabbia_polvere', 'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro'],
    'lunotto_interno':             ['briciole_sabbia_polvere', 'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro'],
    'vetri_laterali_interni':      ['briciole_sabbia_polvere', 'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro'],
    'specchietto_retrovisore_int': ['briciole_sabbia_polvere', 'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro'],
    'parasoli':                    ['briciole_sabbia_polvere', 'alone_vetro', 'prodotto_non_rimosso', 'zona_non_trattata', 'altro'],
    # Vani
    'cofano_interno':  ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'bagagliaio':      ['briciole_sabbia_polvere', 'fango', 'macchia_organica', 'zona_non_trattata', 'altro'],
    'cassetto_guanti': ['briciole_sabbia_polvere', 'macchia_organica', 'zona_non_trattata', 'altro'],
}

ZONE_CATEGORIE = [
    ('Esterno', [
        'carrozzeria_aloni_residui', 'parabrezza_esterno', 'lunotto_esterno',
        'vetri_laterali_esterni', 'specchietti_esterni', 'cerchi',
        'passaruota', 'gomme_nero_gomme', 'paraurti',
    ]),
    ('Interni — Plastiche', [
        'cruscotto_plancia', 'bocchette_aria', 'tunnel_centrale', 'montanti',
        'battitacchi', 'sedile_guidatore', 'sedile_passeggero', 'sedili_posteriori',
        'vani_portiere', 'pannelli_portiere',
    ]),
    ('Interni — Moquette e Tappeti', [
        'moquette_ant_sx', 'moquette_ant_dx', 'moquette_post_sx', 'moquette_post_dx',
        'tappeto_guidatore', 'tappeto_passeggero',
    ]),
    ('Vetri interni', [
        'parabrezza_interno', 'lunotto_interno', 'vetri_laterali_interni',
        'specchietto_retrovisore_int', 'parasoli',
    ]),
    ('Vani', ['cofano_interno', 'bagagliaio', 'cassetto_guanti']),
]

# ---------------------------------------------------------------------------
# Tabella punteggi negativi: (gravità, tipo_rilevatore) → punti
# ---------------------------------------------------------------------------
PUNTEGGI_NEGATIVI = {
    ('bassa', 'interno'): -1,
    ('media', 'interno'): -2,
    ('alta',  'interno'): -4,
    ('bassa', 'esterno'): -2,
    ('media', 'esterno'): -4,
    ('alta',  'esterno'): -8,
}

RILEVATORI_INTERNI = {'responsabile', 'vice'}
RILEVATORI_ESTERNI = {'cliente', 'titolare'}


def tipo_rilevatore(rilevato_da: str) -> str:
    """Restituisce 'interno' o 'esterno' in base al rilevatore."""
    return 'interno' if rilevato_da in RILEVATORI_INTERNI else 'esterno'


def calcola_e_assegna_punteggi(scheda):
    """
    Ricalcola tutti i PunteggioCQ per una scheda.
    Chiamata su post_save di SchedaCQ (esito ok) e dopo ogni DifettoCQ.
    Cancella i punteggi precedenti prima di ricalcolare.
    """
    from apps.cq.models import PunteggioCQ, TipoPunteggio

    # Cancella punteggi precedenti legati a questa scheda
    PunteggioCQ.objects.filter(scheda=scheda).delete()

    mese = scheda.data_ora.month
    anno = scheda.data_ora.year

    if scheda.esito == 'ok':
        _assegna_punteggi_ok(scheda, mese, anno)
    else:
        _assegna_punteggi_non_ok(scheda, mese, anno)


def _assegna_punteggi_ok(scheda, mese, anno):
    """CQ senza difetti: +2 a tutti gli operatori in turno."""
    from apps.cq.models import PunteggioCQ, TipoPunteggio

    operatori_turno = scheda.ordine.operatori_turno.select_related('operatore').all()
    operatori_unici = {ot.operatore for ot in operatori_turno}

    for operatore in operatori_unici:
        PunteggioCQ.objects.create(
            scheda=scheda,
            difetto=None,
            operatore=operatore,
            punti=2,
            tipo=TipoPunteggio.POSITIVO,
            mese=mese,
            anno=anno,
            motivazione=f"CQ ordine {scheda.ordine.numero_progressivo} senza difetti — +2 a tutto il turno",
        )


def _assegna_punteggi_non_ok(scheda, mese, anno):
    """CQ con difetti: calcola punteggi negativi per ogni difetto."""
    from apps.cq.models import PunteggioCQ, TipoPunteggio, ZonaConfig

    tipo_rilev = tipo_rilevatore(scheda.rilevato_da)
    turno = _mappa_turno(scheda.ordine)

    # Precarica mappa zona_codice → postazioni_catena dal DB
    zona_catena_map = {z.codice: z.postazioni_catena for z in ZonaConfig.objects.all()}

    for difetto in scheda.difetti.all():
        punteggio_base = PUNTEGGI_NEGATIVI.get((difetto.gravita, tipo_rilev), 0)
        postazione_produttore = difetto.postazione_responsabile  # come indicato sulla scheda
        catena = list(zona_catena_map.get(difetto.zona, []))

        # 1. Punteggio al produttore del difetto
        operatore_produttore = turno.get(postazione_produttore)
        if operatore_produttore:
            PunteggioCQ.objects.create(
                scheda=scheda,
                difetto=difetto,
                operatore=operatore_produttore,
                punti=punteggio_base,
                tipo=TipoPunteggio.NEG_PRODUTTORE,
                mese=mese,
                anno=anno,
                motivazione=(
                    f"Difetto: {difetto.zona_nome} "
                    f"({difetto.get_gravita_display()}) — produttore"
                ),
            )

        # 2. Catena intermedia (50%) — escludi il produttore e controllo_finale
        catena_intermedia = [p for p in catena if p != postazione_produttore and p != 'controllo_finale']
        for postazione in catena_intermedia:
            operatore = turno.get(postazione)
            if operatore and operatore != operatore_produttore:
                PunteggioCQ.objects.create(
                    scheda=scheda,
                    difetto=difetto,
                    operatore=operatore,
                    punti=round(punteggio_base * 0.5),
                    tipo=TipoPunteggio.NEG_CATENA,
                    mese=mese,
                    anno=anno,
                    motivazione=(
                        f"Difetto: {difetto.zona_nome} "
                        f"({difetto.get_gravita_display()}) — catena ({postazione})"
                    ),
                )

        # 3. Controllo finale
        operatore_cf = turno.get('controllo_finale')
        if operatore_cf:
            if tipo_rilev == 'esterno':
                # Il difetto ha superato il controllo finale: punteggio pieno
                # Se l'op. del CF è già il produttore, non duplicare
                if operatore_cf != operatore_produttore:
                    PunteggioCQ.objects.create(
                        scheda=scheda,
                        difetto=difetto,
                        operatore=operatore_cf,
                        punti=punteggio_base,
                        tipo=TipoPunteggio.NEG_RESPONSABILE,
                        mese=mese,
                        anno=anno,
                        motivazione=(
                            f"Difetto: {difetto.zona_nome} "
                            f"({difetto.get_gravita_display()}) — controllo finale fallito"
                        ),
                    )
            # Se rilevatore interno: il compilatore ha intercettato, CF non paga


def _mappa_turno(ordine) -> dict:
    """
    Restituisce {postazione: operatore_user} per l'ordine dato.
    Se una postazione ha più operatori prende il primo (non previsto normalmente).
    """
    mappa = {}
    for opt in ordine.operatori_turno.select_related('operatore').all():
        if opt.postazione not in mappa:
            mappa[opt.postazione] = opt.operatore
    return mappa


# ---------------------------------------------------------------------------
# Calcolo indici e premi mensili
# ---------------------------------------------------------------------------

def calcola_saldo_grezzo(operatore, anno, mese) -> int:
    """Somma tutti i PunteggioCQ + ModificaPunteggio per operatore/mese."""
    from apps.cq.models import PunteggioCQ, ModificaPunteggio

    punti_cq = PunteggioCQ.objects.filter(
        operatore=operatore, anno=anno, mese=mese
    ).aggregate(totale=models.Sum('punti'))['totale'] or 0

    modifiche = ModificaPunteggio.objects.filter(
        operatore=operatore, anno=anno, mese=mese
    ).aggregate(totale=models.Sum('punti'))['totale'] or 0

    return punti_cq + modifiche


def calcola_turni(operatore, anno, mese) -> int:
    """
    Conta i turni lavorati: ordini unici in cui l'operatore
    era registrato in OperatorePostazioneTurno nel mese.
    """
    from apps.cq.models import OperatorePostazioneTurno

    return (
        OperatorePostazioneTurno.objects
        .filter(
            operatore=operatore,
            ordine__data_ora__year=anno,
            ordine__data_ora__month=mese,
        )
        .values('ordine')
        .distinct()
        .count()
    )


def calcola_indice_mensile(operatore, anno, mese):
    """
    Indice mensile = saldo_grezzo / turni_lavorati.
    Restituisce None se l'operatore non ha turni nel mese.
    """
    turni = calcola_turni(operatore, anno, mese)
    if turni == 0:
        return None
    saldo = calcola_saldo_grezzo(operatore, anno, mese)
    return round(saldo / turni, 4)


def calcola_report_mensile(anno, mese, monte_premi=None):
    """
    Calcola il report mensile per tutti gli operatori attivi nel mese.

    Restituisce una lista di dict:
    {
        'operatore': User,
        'turni': int,
        'saldo_grezzo': int,
        'indice': float|None,
        'premio': Decimal|None,   # None se indice ≤ 0 o monte_premi non impostato
    }
    """
    from django.contrib.auth.models import User
    from apps.cq.models import OperatorePostazioneTurno, ImpostazionePremioMensile, ModificaPunteggio

    # Operatori presenti nel mese: chi ha turni OPPURE chi ha modifiche manuali
    ids_turni = set(
        OperatorePostazioneTurno.objects
        .filter(ordine__data_ora__year=anno, ordine__data_ora__month=mese)
        .values_list('operatore_id', flat=True)
    )
    ids_modifiche = set(
        ModificaPunteggio.objects
        .filter(anno=anno, mese=mese)
        .values_list('operatore_id', flat=True)
    )
    operatori = User.objects.filter(pk__in=ids_turni | ids_modifiche)

    risultati = []
    for op in operatori:
        saldo = calcola_saldo_grezzo(op, anno, mese)
        turni = calcola_turni(op, anno, mese)
        indice = round(saldo / turni, 4) if turni > 0 else None
        risultati.append({
            'operatore': op,
            'turni': turni,
            'saldo_grezzo': saldo,
            'indice': indice,
            'premio': None,
        })

    # Calcola premi
    if monte_premi is None:
        try:
            imp = ImpostazionePremioMensile.objects.get(anno=anno, mese=mese)
            monte_premi = imp.monte_premi
        except ImpostazionePremioMensile.DoesNotExist:
            monte_premi = None

    if monte_premi is not None:
        positivi = [r for r in risultati if r['indice'] is not None and r['indice'] > 0]
        somma_indici = sum(r['indice'] for r in positivi)
        if somma_indici > 0:
            for r in positivi:
                r['premio'] = round(
                    Decimal(str(r['indice'])) / Decimal(str(somma_indici)) * monte_premi,
                    2
                )

    risultati.sort(key=lambda r: (r['indice'] or -9999), reverse=True)
    return risultati


# Import necessario per Sum nella funzione calcola_saldo_grezzo
from django.db import models
