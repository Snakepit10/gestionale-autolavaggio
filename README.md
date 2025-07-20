# Sistema Gestionale Autolavaggio

Sistema completo di gestione ordini per autolavaggio sviluppato in Django con interfaccia per operatori di cassa, dashboard operatori e integrazione con sistemi NFC per abbonamenti.

## Caratteristiche Principali

### 🚗 Gestione Ordini
- Punto cassa con interfaccia touch-friendly
- Gestione ordini con stati (nuovo, in lavorazione, completato)
- Calcolo automatico tempi di attesa
- Stampa scontrini e comande
- Gestione pagamenti multipli

### 👥 Gestione Clienti
- CRUD completo clienti (privati e aziende)
- Sistema punti fedeltà
- Area cliente online con dashboard
- Storico ordini e statistiche personali

### 📋 Sistema Abbonamenti
- Configurazione abbonamenti con wizard multi-step
- Gestione accessi con tessere NFC
- Contatori automatici per servizi inclusi
- Verifica accessi real-time

### 📅 Prenotazioni Online
- Calendario interattivo per prenotazioni
- Configurazione slot personalizzabili
- Conversione automatica prenotazione → ordine
- Sistema di promemoria

### 🏪 Shop Online
- E-commerce integrato per prodotti
- Gestione scorte automatica
- Ordini con ritiro in sede
- Notifiche quando pronto

### 🖥️ Dashboard Postazioni
- Vista real-time per ogni postazione
- Tracking tempi di lavorazione
- Notifiche nuovi ordini
- Gestione code di lavoro

### 📊 Reportistica
- Report vendite e incassi
- Statistiche utilizzo postazioni
- Analytics abbonamenti e prenotazioni
- Export Excel/PDF

### 📱 PWA Mobile
- App mobile per operatori cassa
- Funzionamento offline
- Scanner QR/NFC integrato
- Interfaccia touch ottimizzata

## Requisiti Sistema

### Backend
- Python 3.8+
- Django 4.2+
- PostgreSQL o SQLite
- Redis (per cache e WebSocket)

### Frontend
- Bootstrap 5
- Chart.js per grafici
- HTML5/CSS3/JavaScript

### Opzionali
- Lettore NFC compatibile Web NFC API
- Stampanti termiche di rete (ESC/POS)

## Installazione

### 1. Clona il repository
```bash
git clone <repository-url>
cd gestionale-autolavaggio
```

### 2. Crea ambiente virtuale
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# oppure
venv\Scripts\activate     # Windows
```

### 3. Installa dipendenze
```bash
pip install -r requirements.txt
```

### 4. Configurazione database
```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Crea superuser
```bash
python manage.py createsuperuser
```

### 6. Carica dati iniziali (opzionale)
```bash
python manage.py loaddata fixtures/initial_data.json
```

### 7. Avvia il server
```bash
python manage.py runserver
```

Il sistema sarà disponibile su http://127.0.0.1:8000

## Configurazione Produzione

### 1. Variabili d'ambiente
Crea un file `.env`:
```
DEBUG=False
SECRET_KEY=your-secret-key-here
DATABASE_URL=postgresql://user:password@localhost/dbname
REDIS_URL=redis://localhost:6379/0
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

### 2. Configurazione database PostgreSQL
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'autolavaggio_db',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### 3. Configurazione Redis
```python
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('redis-server', 6379)],
        },
    },
}
```

### 4. Configurazione email
```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@gmail.com'
EMAIL_HOST_PASSWORD = 'your-app-password'
```

## Struttura Progetto

```
gestionale-autolavaggio/
├── apps/
│   ├── core/              # Modelli base (Categorie, Servizi, Postazioni)
│   ├── ordini/            # Gestione ordini e pagamenti
│   ├── clienti/           # Gestione clienti e punti fedeltà
│   ├── postazioni/        # Dashboard postazioni
│   ├── abbonamenti/       # Sistema abbonamenti e NFC
│   ├── prenotazioni/      # Sistema prenotazioni online
│   ├── shop/              # E-commerce integrato
│   ├── reportistica/      # Report e analytics
│   └── api/               # API REST e WebSocket
├── templates/             # Template HTML
├── static/                # File statici (CSS, JS, immagini)
├── config/                # Configurazione Django
└── requirements.txt       # Dipendenze Python
```

## API Endpoints

### Ordini
- `GET /api/ordini/` - Lista ordini
- `POST /api/ordini/` - Crea nuovo ordine
- `GET /api/ordini/{id}/` - Dettaglio ordine
- `PATCH /api/ordini/{id}/stato/` - Aggiorna stato ordine

### Dashboard
- `GET /api/dashboard/stats/` - Statistiche dashboard
- `GET /api/dashboard/chart-ordini/` - Dati grafico ordini

### Abbonamenti
- `POST /api/abbonamenti/verifica-nfc/` - Verifica accesso NFC
- `POST /api/abbonamenti/{id}/registra-accesso/` - Registra accesso

### Postazioni
- `GET /api/postazioni/{id}/ordini/` - Ordini per postazione
- `PATCH /api/postazioni/{id}/aggiorna-item/` - Aggiorna item postazione

## WebSocket Endpoints

### Dashboard Postazioni
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/postazione/1/');
```

### Notifiche Ordini
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/ordini/');
```

## Configurazione NFC

Per abilitare la lettura NFC nel browser:

### 1. HTTPS Obbligatorio
Il Web NFC API funziona solo su HTTPS.

### 2. Supporto Browser
- Chrome Android 89+
- Samsung Internet 15.0+

### 3. Configurazione Tessere
Le tessere NFC devono contenere record NDEF con il codice abbonamento:
```javascript
// Esempio scrittura tessera NFC
const writer = new NDEFWriter();
await writer.write({
    records: [{
        recordType: "text",
        data: "ABB123456"  // Codice abbonamento
    }]
});
```

## Configurazione Stampanti

### Stampanti Termiche di Rete
Il sistema supporta stampanti termiche ESC/POS via TCP/IP:

1. Configura stampante con IP statico
2. Aggiungi stampante in: Configurazione → Stampanti
3. Testa connessione dal pannello admin

### Template di Stampa
I template sono configurabili per:
- Scontrini cliente
- Comande postazioni
- Tessere abbonamento
- Contratti abbonamento

## Gestione Backup

### Database
```bash
# Backup
python manage.py dumpdata > backup.json

# Ripristino
python manage.py loaddata backup.json
```

### File Media
```bash
# Backup file uploadati
tar -czf media_backup.tar.gz media/
```

## Monitoraggio e Log

### Log Django
```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'autolavaggio.log',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
```

### Monitoraggio Performance
- Installare `django-debug-toolbar` per debug
- Configurare Sentry per monitoring produzione
- Utilizzare `django-silk` per profiling query

## Task Programmati (Celery)

### Configurazione Celery
```bash
# Avvia worker Celery
celery -A config worker -l info

# Avvia scheduler Beat
celery -A config beat -l info
```

### Task Automatici
- Alert scorte basse (giornaliero)
- Reset contatori abbonamenti (configurabile)
- Promemoria prenotazioni (sera prima)
- Report automatici (settimanali)
- Pulizia log vecchi (mensile)

## Testing

### Unit Tests
```bash
python manage.py test
```

### Coverage
```bash
pip install coverage
coverage run --source='.' manage.py test
coverage report
coverage html
```

### Load Testing
```bash
pip install locust
locust -f tests/load_test.py
```

## Troubleshooting

### Problemi Comuni

#### 1. Errore connessione database
- Verifica credenziali in settings.py
- Controlla che il database sia avviato
- Verifica permessi utente database

#### 2. WebSocket non funzionanti
- Verifica che Redis sia attivo
- Controlla configurazione CHANNEL_LAYERS
- Assicurati che ASGI sia configurato

#### 3. Stampanti non raggiungibili
- Verifica IP e porta stampante
- Testa connessione con telnet
- Controlla firewall/antivirus

#### 4. NFC non funziona
- Utilizza HTTPS
- Verifica compatibilità browser
- Controlla permessi sito web

### Debug Mode
Per abilitare il debug:
```python
DEBUG = True
ALLOWED_HOSTS = ['*']
```

### Reset Database
```bash
python manage.py flush
python manage.py migrate
python manage.py createsuperuser
```

## Contribuire

1. Fork del repository
2. Crea branch feature (`git checkout -b feature/nuova-funzionalita`)
3. Commit delle modifiche (`git commit -am 'Aggiunge nuova funzionalità'`)
4. Push del branch (`git push origin feature/nuova-funzionalita`)
5. Crea Pull Request

## Licenza

Questo progetto è distribuito sotto licenza MIT. Vedi il file `LICENSE` per i dettagli.

## Supporto

Per supporto tecnico o segnalazione bug:
- Apri una issue su GitHub
- Contatta il team di sviluppo
- Consulta la documentazione API

## Roadmap

### Versione 2.0
- [ ] App mobile nativa (React Native)
- [ ] Integrazione POS automatici
- [ ] Sistema di recensioni clienti
- [ ] Multi-sede e franchising
- [ ] AI per ottimizzazione tempi

### Versione 2.1
- [ ] Integrazione contabilità
- [ ] CRM avanzato
- [ ] Marketing automation
- [ ] Analytics predittive
- [ ] API pubblica per partner