import json as _json
import os
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse, JsonResponse
from django.views.generic import TemplateView
from django.views.static import serve as static_serve

# Health check endpoint per Railway
def health_check(request):
    return HttpResponse("OK", status=200)


def assetlinks_json(request):
    """Serve /.well-known/assetlinks.json per Digital Asset Links (TWA Android).

    Permette alla Trusted Web Activity Android di certificare che e' autorizzata
    a presentare questo dominio senza URL bar.

    Configurazione via env var:
      TWA_ANDROID_PACKAGE_NAME  - es. it.autolavaggiomasterwash.app
      TWA_SHA256_FINGERPRINTS   - comma-separated, es. "AA:BB:...,CC:DD:..."
                                  (output di `keytool -list -v -keystore ...`)
    """
    package_name = os.environ.get('TWA_ANDROID_PACKAGE_NAME', '')
    fp_raw = os.environ.get('TWA_SHA256_FINGERPRINTS', '')
    fingerprints = [f.strip() for f in fp_raw.split(',') if f.strip()]
    if not package_name or not fingerprints:
        # Restituisci array vuoto valido finche non configurato
        return JsonResponse([], safe=False)
    data = [{
        'relation': ['delegate_permission/common.handle_all_urls'],
        'target': {
            'namespace': 'android_app',
            'package_name': package_name,
            'sha256_cert_fingerprints': fingerprints,
        },
    }]
    response = JsonResponse(data, safe=False)
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def apple_app_site_association(request):
    """Serve /.well-known/apple-app-site-association per iOS Universal Links.

    Configurazione via env var:
      IOS_APP_ID_PREFIX  - Team ID Apple Developer (10 char)
      IOS_BUNDLE_ID      - Bundle identifier dell'app
    """
    team_id = os.environ.get('IOS_APP_ID_PREFIX', '')
    bundle_id = os.environ.get('IOS_BUNDLE_ID', '')
    if not team_id or not bundle_id:
        return JsonResponse({}, safe=False)
    data = {
        'applinks': {
            'apps': [],
            'details': [{
                'appID': f'{team_id}.{bundle_id}',
                'paths': ['/app/*'],
            }],
        },
    }
    # AASA deve essere servita con Content-Type application/json e SENZA estensione .json
    response = HttpResponse(_json.dumps(data), content_type='application/json')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def _service_worker(request):
    """Serve /service-worker.js dalla root con scope corretto.

    Il SW deve essere alla root del dominio per intercettare tutto il sito.
    Cerca il file prima in STATIC_ROOT (post-collectstatic), poi nelle
    STATICFILES_DIRS (dev), poi in <BASE_DIR>/static.
    """
    candidates = []
    if settings.STATIC_ROOT:
        candidates.append(settings.STATIC_ROOT)
    candidates.extend(settings.STATICFILES_DIRS)
    candidates.append(os.path.join(settings.BASE_DIR, 'static'))
    for d in candidates:
        full = os.path.join(d, 'service-worker.js')
        if os.path.exists(full):
            response = static_serve(request, path='service-worker.js', document_root=d)
            response['Service-Worker-Allowed'] = '/'
            response['Cache-Control'] = 'no-cache'
            return response
    return HttpResponse('// service-worker.js not found', status=404, content_type='application/javascript')


urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('admin/', admin.site.urls),

    # PWA: service worker dalla root (scope /) e pagina offline
    path('service-worker.js', _service_worker, name='service-worker'),
    path('offline.html', TemplateView.as_view(template_name='offline.html'), name='offline'),

    # TWA/Universal Links: Digital Asset Links Android + AASA iOS
    path('.well-known/assetlinks.json', assetlinks_json, name='assetlinks'),
    path('.well-known/apple-app-site-association', apple_app_site_association, name='aasa'),

    # Sistema di Autenticazione
    path('auth/', include('apps.auth_system.urls')),

    # Lato cliente (frontend pubblico PWA): landing, register, booking, dashboard
    path('app/', include('apps.clients.urls')),

    # Core URLs essenziali (dashboard staff su /, gestisce dispatch interno)
    path('', include('apps.core.urls')),
    path('clienti/', include('apps.clienti.urls')),
    path('ordini/', include('apps.ordini.urls')),
    path('postazioni/', include('apps.postazioni.urls')),

    # Abbonamenti e NFC
    path('abbonamenti/', include('apps.abbonamenti.urls')),

    # Prenotazioni
    path('prenotazioni/', include('apps.prenotazioni.urls')),

    # Gestione finanziaria
    path('finanze/', include('apps.finanze.urls')),

    # Controllo qualità e premi/sanzioni
    path('cq/', include('apps.cq.urls', namespace='cq')),

    # Turni operatore
    path('turni/', include('apps.turni.urls', namespace='turni')),

    # path('shop/', include('apps.shop.urls')),
    # path('cassa/', include('apps.ordini.urls', namespace='cassa')),
    # path('report/', include('apps.reportistica.urls')),  # Temporarily disabled
    # path('api/', include('apps.api.urls')),
    # path('', include('pwa.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)