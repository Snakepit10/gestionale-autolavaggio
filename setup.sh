#!/bin/bash

echo "========================================"
echo "   Setup Gestionale Autolavaggio"
echo "========================================"
echo

# Controlla se Python 3 è installato
if ! command -v python3 &> /dev/null; then
    echo "ERRORE: Python 3 non trovato. Installalo prima di continuare."
    exit 1
fi

echo "Python version:"
python3 --version
echo

# Installa pip se non presente
if ! python3 -m pip --version &> /dev/null; then
    echo "Installazione pip..."
    curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    python3 get-pip.py --user
    rm get-pip.py
    export PATH="$HOME/.local/bin:$PATH"
fi

# Crea virtual environment se possibile
echo "Tentativo creazione virtual environment..."
if python3 -m venv venv 2>/dev/null; then
    echo "Virtual environment creato con successo!"
    source venv/bin/activate
    PIP_CMD="pip"
else
    echo "Virtual environment non disponibile, uso installazione globale utente..."
    PIP_CMD="python3 -m pip --user"
fi

echo
echo "Aggiornamento pip..."
$PIP_CMD install --upgrade pip

echo
echo "Installazione dipendenze base..."
$PIP_CMD install Django
if [[ -f "requirements-basic.txt" ]]; then
    $PIP_CMD install -r requirements-basic.txt
else
    $PIP_CMD install -r requirements.txt
fi

echo
echo "Controllo installazione Django..."
python3 -c "import django; print('Django version:', django.get_version())"

echo
echo "Creazione database..."
python3 manage.py makemigrations
python3 manage.py migrate

echo
echo "Vuoi creare un superuser? (y/n)"
read -r create_super
if [[ $create_super == "y" || $create_super == "Y" ]]; then
    python3 manage.py createsuperuser
fi

echo
echo "========================================"
echo "   Setup completato!"
echo "========================================"
echo
echo "Per avviare il server:"
if [[ -d "venv" ]]; then
    echo "  1. Attiva virtual environment: source venv/bin/activate"
fi
echo "  2. Avvia server: python3 manage.py runserver"
echo