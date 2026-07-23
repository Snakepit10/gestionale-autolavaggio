#!/bin/sh
# =====================================================================
# Entrypoint del broker: materializza il file password dalla variabile
# d'ambiente MOSQUITTO_PASSWD prima di avviare Mosquitto.
#
# MOSQUITTO_PASSWD contiene le righe "utente:hash" generate IN LOCALE
# con mosquitto_passwd (vedi README). Cosi' le credenziali vivono solo
# nelle variabili del servizio Railway: niente file segreti nel repo,
# niente upload manuali sul volume.
# =====================================================================
set -e

if [ -n "$MOSQUITTO_PASSWD" ]; then
    printf '%s\n' "$MOSQUITTO_PASSWD" > /mosquitto/config/passwd
fi

if [ ! -f /mosquitto/config/passwd ]; then
    echo "ERRORE: file passwd mancante. Imposta la variabile MOSQUITTO_PASSWD" >&2
    echo "        con il contenuto generato da mosquitto_passwd (vedi README)." >&2
    exit 1
fi

# Mosquitto 2.x rifiuta password_file con permessi larghi: stringiamo
# e assegniamo all'utente mosquitto con cui gira il processo.
chmod 0700 /mosquitto/config/passwd
chown mosquitto:mosquitto /mosquitto/config/passwd 2>/dev/null || true
chown -R mosquitto:mosquitto /mosquitto/data 2>/dev/null || true

# Delega all'entrypoint ufficiale dell'immagine (drop dei privilegi ecc.)
exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
