"""Spia MQTT da terminale: stampa ogni messaggio che passa dal broker.

Sostituto minimale di MQTT Explorer per il collaudo. Uso:

    python mosquitto-broker/spia_topics.py HOST PORTA UTENTE PASSWORD

Esempio (endpoint TCP Proxy di Railway, utente debug):

    python mosquitto-broker/spia_topics.py tramway.proxy.rlwy.net 12345 debug LaPasswordDebug

Poi genera un impulso sullo Shelly e guarda cosa compare (topic e
payload). Ctrl+C per uscire.
"""
import sys

import paho.mqtt.client as mqtt


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        raise SystemExit(1)
    host, porta, utente, password = sys.argv[1:5]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='debug-spia')
    client.username_pw_set(utente, password)

    def on_connect(cl, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f'[ok] connesso a {host}:{porta}, in ascolto su TUTTI i topic (#)...')
            cl.subscribe('#', qos=0)
        else:
            print(f'[ERRORE] connessione rifiutata: {reason_code}')

    def on_message(cl, userdata, msg):
        try:
            corpo = msg.payload.decode('utf-8')
        except UnicodeDecodeError:
            corpo = repr(msg.payload)
        print(f'\n>>> {msg.topic}\n    {corpo}')

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, int(porta), keepalive=30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print('\nciao!')


if __name__ == '__main__':
    main()
