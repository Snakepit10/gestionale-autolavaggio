from django.utils import timezone
from datetime import timedelta
from apps.core.models import Postazione
from .models import ItemOrdine, Ordine
# Temporaneamente commentato - da riabilitare quando l'app prenotazioni sarà attiva
# from apps.prenotazioni.models import Prenotazione
from apps.core.models import StampanteRete


class CalcoloTempoAttesaService:
    """Servizio per il calcolo dei tempi di attesa"""
    
    @staticmethod
    def calcola_tempo_attesa_nuovo_ordine(servizi_richiesti):
        """
        Calcola il tempo di attesa per un nuovo ordine
        considerando tutti i fattori
        """
        tempo_attesa_totale = 0
        
        for servizio in servizi_richiesti:
            # 1. Trova postazioni disponibili per il servizio
            postazioni = servizio.postazioni.filter(attiva=True)
            
            if not postazioni.exists():
                # Se nessuna postazione specifica, usa tempo di default
                tempo_attesa_totale = max(tempo_attesa_totale, servizio.durata_minuti)
                continue
            
            tempi_attesa_postazioni = []
            for postazione in postazioni:
                # 2. Calcola carico attuale postazione
                ordini_in_coda = ItemOrdine.objects.filter(
                    postazione_assegnata=postazione,
                    stato__in=['in_attesa', 'in_lavorazione'],
                    ordine__stato__in=['in_attesa', 'in_lavorazione']
                ).select_related('servizio_prodotto')
                
                # 3. Somma durate servizi in coda
                tempo_coda = sum([
                    item.servizio_prodotto.durata_minuti 
                    for item in ordini_in_coda
                ])
                
                # 4. Considera prenotazioni programmate
                # Temporaneamente commentato - da riabilitare con l'app prenotazioni
                # ora_attuale = timezone.now()
                # prenotazioni_future = Prenotazione.objects.filter(
                #     slot__data=ora_attuale.date(),
                #     slot__ora_inizio__gte=ora_attuale.time(),
                #     stato='confermata',
                #     servizi=servizio
                # ).count()
                # tempo_prenotazioni = prenotazioni_future * servizio.durata_minuti
                tempo_prenotazioni = 0  # Placeholder finché l'app prenotazioni non è attiva
                
                # 5. Aggiungi tempo medio storico e buffer sicurezza
                tempo_medio_storico = postazione.get_tempo_medio_servizio(servizio)
                fattore_correzione = 1.1  # 10% buffer sicurezza
                
                tempo_totale_postazione = (
                    tempo_coda + tempo_prenotazioni + tempo_medio_storico
                ) * fattore_correzione
                
                tempi_attesa_postazioni.append(tempo_totale_postazione)
            
            # 6. Prendi il tempo minimo tra le postazioni
            if tempi_attesa_postazioni:
                tempo_attesa_servizio = min(tempi_attesa_postazioni)
            else:
                tempo_attesa_servizio = servizio.durata_minuti
            
            # 7. Il tempo totale è il massimo tra i servizi (possono essere paralleli)
            tempo_attesa_totale = max(tempo_attesa_totale, tempo_attesa_servizio)
        
        # 8. Calcola ora consegna prevista
        ora_consegna_prevista = timezone.now() + timedelta(minutes=int(tempo_attesa_totale))
        
        return {
            'tempo_attesa_minuti': int(tempo_attesa_totale),
            'ora_consegna_prevista': ora_consegna_prevista,
            'consegna_suggerita': ora_consegna_prevista.strftime('%H:%M')
        }
    
    @staticmethod
    def aggiorna_tempi_attesa_real_time():
        """Task per aggiornare i tempi di attesa in tempo reale"""
        # Ordini non completati
        ordini_aperti = Ordine.objects.filter(
            stato__in=['in_attesa', 'in_lavorazione']
        )
        
        for ordine in ordini_aperti:
            servizi = [item.servizio_prodotto for item in ordine.items.filter(
                servizio_prodotto__tipo='servizio'
            )]
            
            if servizi:
                nuovo_tempo = CalcoloTempoAttesaService.calcola_tempo_attesa_nuovo_ordine(servizi)
                ordine.tempo_attesa_minuti = nuovo_tempo['tempo_attesa_minuti']
                ordine.ora_consegna_prevista = nuovo_tempo['ora_consegna_prevista']
                ordine.save(update_fields=['tempo_attesa_minuti', 'ora_consegna_prevista'])


class StampaService:
    """Servizio per la gestione delle stampanti"""
    
    @staticmethod
    def stampa_scontrino(ordine):
        """Stampa lo scontrino per un ordine"""
        try:
            # Trova stampante scontrini predefinita
            stampante = StampanteRete.objects.filter(
                tipo='scontrino',
                attiva=True,
                predefinita=True
            ).first()
            
            if not stampante:
                stampante = StampanteRete.objects.filter(
                    tipo='scontrino',
                    attiva=True
                ).first()
            
            if not stampante:
                raise Exception("Nessuna stampante scontrini configurata")
            
            # Genera contenuto scontrino
            contenuto = StampaService._genera_scontrino(ordine, stampante)
            
            # Invia alla stampante
            StampaService._invia_stampa(stampante, contenuto)
            
            return True
            
        except Exception as e:
            raise Exception(f"Errore stampa scontrino: {str(e)}")
    
    @staticmethod
    def stampa_comanda(ordine, postazione):
        """Stampa comanda per una postazione specifica"""
        try:
            # Usa stampante della postazione o predefinita
            stampante = postazione.stampante_comande
            
            if not stampante:
                stampante = StampanteRete.objects.filter(
                    tipo='comanda',
                    attiva=True,
                    predefinita=True
                ).first()
            
            if not stampante:
                raise Exception("Nessuna stampante comande configurata")
            
            # Filtra items per la postazione
            items = ordine.items.filter(postazione_assegnata=postazione)
            
            if not items.exists():
                raise Exception("Nessun item per questa postazione")
            
            # Genera contenuto comanda
            contenuto = StampaService._genera_comanda(ordine, postazione, items, stampante)
            
            # Invia alla stampante
            StampaService._invia_stampa(stampante, contenuto)
            
            return True
            
        except Exception as e:
            raise Exception(f"Errore stampa comanda: {str(e)}")
    
    @staticmethod
    def _genera_scontrino(ordine, stampante):
        """Genera il contenuto dello scontrino"""
        larghezza = stampante.larghezza_carta
        separatore = "-" * (larghezza // 2)
        
        contenuto = []
        contenuto.append("AUTOLAVAGGIO")
        contenuto.append("Via Roma 123, Milano")
        contenuto.append("Tel: 02-1234567")
        contenuto.append(separatore)
        contenuto.append(f"SCONTRINO #{ordine.numero_progressivo}")
        contenuto.append(f"Data: {ordine.data_ora.strftime('%d/%m/%Y %H:%M')}")
        contenuto.append(f"Operatore: {ordine.operatore.username if ordine.operatore else 'N/A'}")
        
        if ordine.cliente:
            contenuto.append(f"Cliente: {ordine.cliente}")
        
        contenuto.append(separatore)
        
        # Items
        for item in ordine.items.all():
            linea = f"{item.servizio_prodotto.titolo}"
            if item.quantita > 1:
                linea += f" x{item.quantita}"
            
            prezzo_str = f"€{item.subtotale:.2f}"
            spazi = " " * (larghezza - len(linea) - len(prezzo_str))
            contenuto.append(f"{linea}{spazi}{prezzo_str}")
        
        contenuto.append(separatore)
        
        # Totali
        totale_str = f"TOTALE: €{ordine.totale:.2f}"
        contenuto.append(totale_str)
        
        if ordine.importo_sconto > 0:
            sconto_str = f"Sconto: -€{ordine.importo_sconto:.2f}"
            contenuto.append(sconto_str)
            finale_str = f"TOTALE FINALE: €{ordine.totale_finale:.2f}"
            contenuto.append(finale_str)
        
        # Pagamento
        contenuto.append(f"Pagamento: {ordine.metodo_pagamento}")
        
        if ordine.stato_pagamento == 'pagato':
            contenuto.append("PAGATO")
        else:
            contenuto.append(f"Da pagare: €{ordine.saldo_dovuto:.2f}")
        
        contenuto.append(separatore)
        
        # Tempo attesa
        if ordine.tempo_attesa_minuti > 0:
            contenuto.append(f"Tempo attesa: {ordine.tempo_attesa_minuti} min")
            if ordine.ora_consegna_prevista:
                contenuto.append(f"Pronto alle: {ordine.ora_consegna_prevista.strftime('%H:%M')}")
        
        contenuto.append("")
        contenuto.append("Grazie per la visita!")
        contenuto.append("")
        
        return "\n".join(contenuto)
    
    @staticmethod
    def _genera_comanda(ordine, postazione, items, stampante):
        """Genera il contenuto della comanda"""
        larghezza = stampante.larghezza_carta
        separatore = "-" * (larghezza // 2)
        
        contenuto = []
        contenuto.append(f"COMANDA - {postazione.nome.upper()}")
        contenuto.append(separatore)
        contenuto.append(f"Ordine: #{ordine.numero_progressivo}")
        contenuto.append(f"Data: {ordine.data_ora.strftime('%d/%m/%Y %H:%M')}")
        
        if ordine.cliente:
            contenuto.append(f"Cliente: {ordine.cliente}")
        
        if ordine.ora_consegna_prevista:
            contenuto.append(f"Consegna: {ordine.ora_consegna_prevista.strftime('%H:%M')}")
        
        contenuto.append(separatore)
        
        # Items per questa postazione
        for item in items:
            contenuto.append(f"* {item.servizio_prodotto.titolo}")
            if item.quantita > 1:
                contenuto.append(f"  Quantità: {item.quantita}")
            
            # Note specifiche
            if hasattr(item, 'note') and item.note:
                contenuto.append(f"  Note: {item.note}")
        
        contenuto.append(separatore)
        
        if ordine.nota:
            contenuto.append("NOTE ORDINE:")
            contenuto.append(ordine.nota)
            contenuto.append(separatore)
        
        contenuto.append(f"Stampato: {timezone.now().strftime('%H:%M:%S')}")
        contenuto.append("")
        
        return "\n".join(contenuto)
    
    @staticmethod
    def _invia_stampa(stampante, contenuto):
        """Invia il contenuto alla stampante di rete"""
        import socket
        
        try:
            # Connessione TCP alla stampante
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((stampante.indirizzo_ip, stampante.porta))
            
            # Comandi ESC/POS di base
            comandi_escpos = [
                b'\x1B\x40',  # Inizializza stampante
                contenuto.encode('utf-8', errors='ignore'),  # Contenuto
                b'\x1D\x56\x42\x00',  # Taglia carta (se supportato)
            ]
            
            for comando in comandi_escpos:
                sock.send(comando)
            
            sock.close()
            
        except socket.timeout:
            raise Exception(f"Timeout connessione con {stampante.nome}")
        except socket.error as e:
            raise Exception(f"Errore connessione con {stampante.nome}: {str(e)}")
        except Exception as e:
            raise Exception(f"Errore invio stampa: {str(e)}")