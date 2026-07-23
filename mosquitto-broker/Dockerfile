# Broker MQTT per Railway: immagine ufficiale + config nel repo.
# Il file password NON e' nell'immagine: viene scritto all'avvio
# dall'entrypoint a partire dalla variabile MOSQUITTO_PASSWD.
FROM eclipse-mosquitto:2

COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY entrypoint.sh /entrypoint-autolavaggio.sh
RUN chmod +x /entrypoint-autolavaggio.sh

ENTRYPOINT ["/entrypoint-autolavaggio.sh"]
