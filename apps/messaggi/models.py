"""Modelli per le conversazioni WhatsApp dei clienti.

Una `ConversazioneWhatsApp` rappresenta lo storico messaggi con un
singolo numero E.164. Il legame al `Cliente` e' opzionale (None per
numeri sconosciuti che hanno scritto al business prima di essere
anagrafati).

Ogni `MessaggioWhatsApp` ha:
- direzione 'in' (cliente -> noi) o 'out' (noi -> cliente)
- corpo testo (per ora supportiamo solo text; media in fase 2)
- wa_message_id Meta per dedup webhook (Meta puo' rinotificare lo
  stesso messaggio)
- stato di consegna (sent/delivered/read/failed) aggiornato dai
  webhook di status update
"""
from django.conf import settings
from django.db import models


class ConversazioneWhatsApp(models.Model):
    """Storico messaggi con un singolo numero. Unique per numero E.164."""

    numero_e164 = models.CharField(
        max_length=20, unique=True, db_index=True,
        help_text="Numero E.164 con prefisso +, es. +393792337051"
    )
    cliente = models.ForeignKey(
        'clienti.Cliente',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='conversazioni_wa',
        help_text="Cliente anagrafato associato. Null se numero sconosciuto."
    )
    # Aggiornato ad ogni messaggio in o out (auto_now) -> per ordinare la lista
    ultimo_messaggio_il = models.DateTimeField(auto_now=True, db_index=True)
    # Solo quando arriva un messaggio incoming -> usato per finestra 24h
    ultimo_incoming_il = models.DateTimeField(null=True, blank=True)
    non_letti = models.PositiveIntegerField(default=0)
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ultimo_messaggio_il']
        verbose_name = 'Conversazione WhatsApp'
        verbose_name_plural = 'Conversazioni WhatsApp'

    def __str__(self):
        nome = self.cliente.nome if self.cliente else 'Sconosciuto'
        return f'{nome} ({self.numero_e164})'

    def finestra_24h_aperta(self) -> bool:
        """Vero se l'ultimo messaggio incoming e' < 24h fa.

        Solo dentro questa finestra WhatsApp permette di rispondere con
        testo libero; oltre serve un template approvato.
        """
        from django.utils import timezone
        from datetime import timedelta
        if not self.ultimo_incoming_il:
            return False
        return (timezone.now() - self.ultimo_incoming_il) < timedelta(hours=24)


class MessaggioWhatsApp(models.Model):
    """Singolo messaggio in una conversazione WhatsApp."""

    DIREZIONE = [
        ('in', 'Entrata'),     # cliente -> business
        ('out', 'Uscita'),     # business -> cliente
    ]
    STATO = [
        ('received', 'Ricevuto'),   # incoming: arrivato dal cliente
        ('sent', 'Inviato'),         # outgoing: passato a Meta
        ('delivered', 'Recapitato'), # outgoing: telefono cliente ha ricevuto
        ('read', 'Letto'),           # outgoing: cliente ha aperto
        ('failed', 'Fallito'),       # outgoing: Meta ha rigettato
    ]

    conversazione = models.ForeignKey(
        ConversazioneWhatsApp,
        on_delete=models.CASCADE,
        related_name='messaggi',
    )
    direzione = models.CharField(max_length=3, choices=DIREZIONE)
    corpo = models.TextField(blank=True, default='')
    # ID del messaggio lato Meta. Per incoming arriva nel payload del
    # webhook; per outgoing torna nella response API messages.
    # Lo usiamo per dedup (Meta puo' rinotificare) e per matchare
    # status update lato outgoing.
    wa_message_id = models.CharField(
        max_length=128, blank=True, default='', db_index=True,
    )
    stato = models.CharField(max_length=12, choices=STATO, default='received')
    timestamp_meta = models.DateTimeField(null=True, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    operatore = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text="Chi ha inviato il messaggio. Solo per direzione=out.",
    )

    class Meta:
        ordering = ['creato_il']
        verbose_name = 'Messaggio WhatsApp'
        verbose_name_plural = 'Messaggi WhatsApp'
        # Dedup webhook: stesso messaggio + stessa conversazione = stesso record.
        # constraint si applica solo a wa_message_id non vuoto in modo soft:
        # outgoing prima di avere id resta indistinguibile, ma noi popoliamo
        # subito dopo la send.
        constraints = [
            models.UniqueConstraint(
                fields=['conversazione', 'wa_message_id'],
                condition=models.Q(wa_message_id__gt=''),
                name='unique_msg_per_conv_when_wa_id_set',
            ),
        ]

    def __str__(self):
        prefix = '>' if self.direzione == 'in' else '<'
        return f'{prefix} {self.corpo[:50]}'
