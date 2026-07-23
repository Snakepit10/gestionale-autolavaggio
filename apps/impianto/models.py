from django.db import models


class EventoImpianto(models.Model):
    """Un evento arrivato dai dispositivi dell'impianto via MQTT.

    Esempi: aggiornamento del contatore impulsi (gettoniera) dello
    Shelly di pista2, eventi di input dal portale, ecc. Il payload
    grezzo viene conservato per debug/riconciliazioni future.
    """

    # Identificativo del nodo, estratto dal topic MQTT:
    # autolavaggio/<nodo>/events/rpc -> 'pista2', 'portale1', ...
    nodo = models.CharField(max_length=50, db_index=True)

    # Tipo di evento normalizzato: 'contatore' per gli aggiornamenti
    # del contatore impulsi, oppure 'componente:evento' per i
    # NotifyEvent Shelly (es. 'input:2:single_push').
    tipo_evento = models.CharField(max_length=80)

    # Valore numerico dell'evento (es. totale del contatore impulsi).
    # Null per gli eventi senza valore.
    valore = models.BigIntegerField(null=True, blank=True)

    # Payload JSON grezzo cosi' come arrivato dal dispositivo
    payload = models.JSONField(default=dict, blank=True)

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Evento impianto'
        verbose_name_plural = 'Eventi impianto'
        indexes = [
            models.Index(fields=['nodo', 'tipo_evento', 'timestamp']),
        ]

    def __str__(self):
        val = f' = {self.valore}' if self.valore is not None else ''
        return f'[{self.nodo}] {self.tipo_evento}{val} @ {self.timestamp:%d/%m %H:%M:%S}'
