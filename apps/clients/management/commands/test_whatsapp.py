"""Invia un messaggio WhatsApp di test senza passare per una prenotazione.

Usato per validare il setup Meta (Phone ID, token, permessi, numero
destinatario tra i tester approvati) prima di andare in produzione
con i template ufficiali.

Esempi:
  # Invia hello_world (default Meta, sempre approvato) al tuo numero
  python manage.py test_whatsapp +393792337051 --template hello_world --lang en_US

  # Manda un template approvato con parametri
  python manage.py test_whatsapp +393792337051 --template prenotazione_ricevuta \\
      --param Mario --param 15/06/2026 --param 14:00 --param Lavaggio --param ABC123

  # Diagnostica: ritorna anche la response body completa
  python manage.py test_whatsapp +393792337051 --template hello_world --lang en_US --verbose
"""
import json

import requests
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from apps.clients.whatsapp import _to_e164


class Command(BaseCommand):
    help = 'Invia un test WhatsApp diretto a un numero.'

    def add_arguments(self, parser):
        parser.add_argument('numero', help='Numero destinatario (es. +393792337051)')
        parser.add_argument('--template', default='hello_world',
                            help='Nome template Meta (default: hello_world)')
        parser.add_argument('--lang', default=None,
                            help='Codice lingua (default: env META_WHATSAPP_TEMPLATE_LANG; usa en_US per hello_world)')
        parser.add_argument('--param', action='append', default=[],
                            help='Parametro body (ripetibile, in ordine). Omettere per hello_world.')
        parser.add_argument('--verbose', action='store_true',
                            help='Stampa anche payload e response completa')

    def handle(self, *args, **opts):
        numero = opts['numero']
        template = opts['template']
        lang = opts['lang'] or settings.META_WHATSAPP_TEMPLATE_LANG
        params = opts['param']
        verbose = opts['verbose']

        if not settings.WHATSAPP_ENABLED:
            raise CommandError(
                'WHATSAPP_ENABLED=False. Configura META_WHATSAPP_PHONE_ID + '
                'META_WHATSAPP_ACCESS_TOKEN nelle env vars.'
            )

        to = _to_e164(numero)
        if not to:
            raise CommandError(f'Numero non valido (non parsabile come E.164): {numero}')

        url = (
            f"https://graph.facebook.com/{settings.META_WHATSAPP_API_VERSION}"
            f"/{settings.META_WHATSAPP_PHONE_ID}/messages"
        )
        headers = {
            'Authorization': f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
        }
        components = []
        if params:
            components.append({
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(p)} for p in params],
            })
        payload = {
            'messaging_product': 'whatsapp',
            'to': to.lstrip('+'),
            'type': 'template',
            'template': {
                'name': template,
                'language': {'code': lang},
                'components': components,
            },
        }

        self.stdout.write(f'POST {url}')
        self.stdout.write(f'  to:       {to}')
        self.stdout.write(f'  template: {template}  lang={lang}')
        self.stdout.write(f'  params:   {params}')
        if verbose:
            self.stdout.write('Payload:\n' + json.dumps(payload, indent=2))

        try:
            r = requests.post(url, json=payload, headers=headers, timeout=10)
        except requests.RequestException as e:
            raise CommandError(f'Errore di rete: {e}')

        if r.status_code >= 400:
            self.stderr.write(self.style.ERROR(f'HTTP {r.status_code}'))
            self.stderr.write(self.style.ERROR(r.text))
            return
        self.stdout.write(self.style.SUCCESS(f'OK HTTP {r.status_code}'))
        if verbose:
            self.stdout.write('Response:\n' + r.text)
        else:
            try:
                data = r.json()
                if 'messages' in data:
                    self.stdout.write('Message ID(s): ' + ', '.join(m.get('id', '?') for m in data['messages']))
            except Exception:
                pass
