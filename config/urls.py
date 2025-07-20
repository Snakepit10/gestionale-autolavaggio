from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Core URLs essenziali
    path('', include('apps.core.urls')),
    path('clienti/', include('apps.clienti.urls')),
    path('ordini/', include('apps.ordini.urls')),
    path('postazioni/', include('apps.postazioni.urls')),
    
    # Abbonamenti e NFC
    path('abbonamenti/', include('apps.abbonamenti.urls')),
    
    # Prenotazioni
    path('prenotazioni/', include('apps.prenotazioni.urls')),
    # path('shop/', include('apps.shop.urls')),
    # path('cassa/', include('apps.ordini.urls', namespace='cassa')),
    # path('report/', include('apps.reportistica.urls')),  # Temporarily disabled
    # path('api/', include('apps.api.urls')),
    # path('', include('pwa.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)