#!/usr/bin/env python
import os
import sys
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_export')
django.setup()

from django.core.management import call_command
import io
import re

# Crea un buffer in memoria per catturare l'output
output = io.StringIO()

# Lista delle app da esportare
apps_to_export = ['core', 'clienti', 'ordini', 'abbonamenti', 'prenotazioni']

try:
    # Esporta usando il comando Django nativo in memoria
    call_command('dumpdata', *apps_to_export, 
                format='json', 
                indent=2,
                natural_foreign=True, 
                natural_primary=True,
                exclude=['contenttypes', 'auth.Permission'],
                stdout=output)
    
    # Ottieni il contenuto
    content = output.getvalue()
    
    # Pulisci caratteri problematici
    def clean_text(text):
        # Rimuove caratteri di controllo Unicode
        text = re.sub(r'[\u202a-\u202e\u2066-\u2069]', '', text)
        # Rimuove altri caratteri problematici
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text
    
    clean_content = clean_text(content)
    
    # Salva con encoding UTF-8 esplicito
    with open('data_export.json', 'w', encoding='utf-8', newline='') as f:
        f.write(clean_content)
    
    print(f"Export completato! File salvato: {len(clean_content)} caratteri")
    
except Exception as e:
    print(f"Errore durante l'export: {e}")
    
finally:
    output.close()