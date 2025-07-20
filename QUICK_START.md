# 🚀 Quick Start Guide

Se hai riscontrato l'errore `ModuleNotFoundError: No module named 'django'`, segui questa guida per risolvere rapidamente.

## ❗ Errore Comune: Django non trovato

```
ModuleNotFoundError: No module named 'django'
ImportError: Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH environment variable? Did you forget to activate a virtual environment?
```

## 🛠️ Soluzioni Rapide

### 🪟 Su Windows

1. **Metodo 1: Setup Automatico**
   ```cmd
   setup.bat
   ```

2. **Metodo 2: Manuale**
   ```cmd
   python -m pip install Django
   python -m pip install -r requirements.txt
   python manage.py runserver
   ```

3. **Metodo 3: Virtual Environment**
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver
   ```

### 🐧 Su Linux/WSL

1. **Setup Automatico**
   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

2. **Installazione Manuale**
   ```bash
   # Installa pip se mancante
   sudo apt update
   sudo apt install python3-pip python3-venv
   
   # Crea virtual environment
   python3 -m venv venv
   source venv/bin/activate
   
   # Installa dipendenze
   pip install -r requirements.txt
   
   # Setup database
   python manage.py migrate
   
   # Avvia server
   python manage.py runserver
   ```

### 🍎 Su macOS

```bash
# Assicurati di avere Python 3
python3 --version

# Crea virtual environment
python3 -m venv venv
source venv/bin/activate

# Installa dipendenze
pip install -r requirements.txt

# Setup database
python manage.py migrate

# Avvia server
python manage.py runserver
```

## 🔍 Verifica Installazione

Dopo l'installazione, verifica che tutto funzioni:

```bash
python -c "import django; print('Django version:', django.get_version())"
```

Dovrebbe mostrare la versione di Django installata.

## 🌐 Accesso al Sistema

Una volta avviato il server con `python manage.py runserver`, vai su:

- **Homepage:** http://127.0.0.1:8000/
- **Admin:** http://127.0.0.1:8000/admin/
- **POS Cassa:** http://127.0.0.1:8000/ordini/cassa/
- **Mobile PWA:** http://127.0.0.1:8000/ordini/cassa/mobile/

## 👤 Primo Accesso

1. **Crea superuser:**
   ```bash
   python manage.py createsuperuser
   ```

2. **Accedi all'admin** con le credenziali create

3. **Configura i dati base:**
   - Categorie servizi
   - Servizi/Prodotti
   - Postazioni
   - Eventualmente clienti di test

## 🔧 Problemi Comuni

### Python non trovato
```bash
# Su Linux/WSL
sudo apt install python3

# Su Windows - scarica da python.org
```

### Pip non installato
```bash
# Su Linux/WSL
sudo apt install python3-pip

# Su Windows - dovrebbe essere incluso con Python
```

### Virtual environment non creabile
```bash
# Su Linux/WSL
sudo apt install python3-venv

# Su Windows - usa conda o installa python completo
```

### Port già in uso
```bash
# Usa una porta diversa
python manage.py runserver 8080
```

## 📞 Serve Aiuto?

Se continui ad avere problemi:

1. Verifica di avere Python 3.8+ installato
2. Usa sempre un virtual environment
3. Assicurati che pip sia aggiornato: `pip install --upgrade pip`
4. Su WSL, potrebbe servire installare build tools: `sudo apt install build-essential`

## ⚡ TL;DR - Comandi Rapidi

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
pip install Django
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

**Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Poi vai su: http://127.0.0.1:8000/ 🎉