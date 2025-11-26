"""
CONFIGURAZIONE DJANGO CHE FUNZIONA GARANTITO
"""
import os
from pathlib import Path

# Directory base del progetto
BASE_DIR = Path(__file__).resolve().parent.parent

# Chiave segreta - da variabile d'ambiente in produzione
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-secret-key-for-development-change-in-production')

# Debug - False in produzione
DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

# Host consentiti
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']

# CSRF trusted origins for Railway
CSRF_TRUSTED_ORIGINS = [
    'https://gestionale-autolavaggio-production.up.railway.app',
    'http://gestionale-autolavaggio-production.up.railway.app',
]

# Applicazioni Django
INSTALLED_APPS = [
    'admin_interface',
    'colorfield',
    'django.contrib.admin',
    'django.contrib.auth', 
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'crispy_forms',
    'crispy_bootstrap5',
    'formtools',
    'channels',

    # App del gestionale autolavaggio (core essenziali)
    'apps.auth_system',
    'apps.core',
    'apps.clienti',
    'apps.ordini',
    'apps.postazioni',
    'apps.abbonamenti',
    'apps.prenotazioni',
    'apps.api',
    # 'apps.reportistica',  # Temporarily disabled due to pandas dependency
    # 'apps.shop',
]

# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.auth_system.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# URL configuration
ROOT_URLCONF = 'config.urls'

# Templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# WSGI application
WSGI_APPLICATION = 'config.wsgi.application'

# Database configuration
import os
import dj_database_url

# Use PostgreSQL on Railway, SQLite locally
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'
    )
}

# Validazione password
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internazionalizzazione
LANGUAGE_CODE = 'it-it'
TIME_ZONE = 'Europe/Rome'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Formati data e ora italiani
DATE_FORMAT = 'd/m/Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'd/m/Y H:i'
SHORT_DATE_FORMAT = 'd/m/Y'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'

# File statici
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [BASE_DIR / 'static']

# Whitenoise configuration for static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Chiave primaria di default
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Authentication settings
LOGIN_URL = '/auth/operatori/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/auth/operatori/login/'

# Session settings
SESSION_COOKIE_AGE = 1209600  # 2 settimane
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False  # True in produzione con HTTPS

# Channels configuration
ASGI_APPLICATION = 'config.asgi.application'

# Channel layers (Redis per WebSocket)
from urllib.parse import urlparse

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
redis_url = urlparse(REDIS_URL)

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [{
                "address": (redis_url.hostname or 'localhost', redis_url.port or 6379),
                "password": redis_url.password,
            }] if redis_url.password else [(redis_url.hostname or 'localhost', redis_url.port or 6379)],
        },
    },
}

# --- FINE CONFIGURAZIONE ---