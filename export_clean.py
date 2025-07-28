#!/usr/bin/env python
import os
import sys
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_export')
django.setup()

from django.core import serializers
from django.apps import apps
import json
import re
from decimal import Decimal
from datetime import datetime, date, time
from uuid import UUID

def clean_string(text):
    """Rimuove caratteri Unicode problematici"""
    if isinstance(text, str):
        # Rimuove caratteri di controllo Unicode
        text = re.sub(r'[\u202a-\u202e\u2066-\u2069]', '', text)
        # Rimuove altri caratteri problematici
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    return text

def clean_object(obj):
    """Pulisce ricorsivamente un oggetto da caratteri problematici e converte tipi non-JSON"""
    if isinstance(obj, dict):
        return {k: clean_object(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_object(item) for item in obj]
    elif isinstance(obj, str):
        return clean_string(obj)
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date, time)):
        return obj.isoformat() if obj else None
    elif isinstance(obj, UUID):
        return str(obj)
    else:
        return obj

# Lista delle app da esportare
apps_to_export = ['core', 'clienti', 'ordini', 'abbonamenti', 'prenotazioni']

# Raccogli tutti i modelli dalle app specificate
models_to_export = []
for app_name in apps_to_export:
    try:
        app = apps.get_app_config(app_name)
        models_to_export.extend(app.get_models())
    except:
        print(f"App {app_name} non trovata, salto...")

print(f"Esporto {len(models_to_export)} modelli...")

# Esporta i dati
data = []
for model in models_to_export:
    print(f"Esporto {model._meta.label}...")
    try:
        queryset = model.objects.all()
        serialized = serializers.serialize('python', queryset, use_natural_foreign_keys=True)
        data.extend(serialized)
    except Exception as e:
        print(f"Errore con {model._meta.label}: {e}")

# Pulisci i dati
print("Pulendo i dati...")
clean_data = clean_object(data)

# Salva in JSON
print("Salvando data_export.json...")
with open('data_export.json', 'w', encoding='utf-8') as f:
    json.dump(clean_data, f, indent=2, ensure_ascii=False)

print(f"Export completato! {len(clean_data)} oggetti esportati.")