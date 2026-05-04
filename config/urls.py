import os
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from django.views.generic import TemplateView
from django.views.static import serve as static_serve

# Health check endpoint per Railway
def health_check(request):
    return HttpResponse("OK", status=200)


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

    # Sistema di Autenticazione
    path('auth/', include('apps.auth_system.urls')),

    # Core URLs essenziali
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