from django.contrib import admin
from django.urls import path
from django.http import HttpResponse
from django.conf import settings
from django.conf.urls.static import static

def home_view(request):
    """Vista temporanea per testare che il server funzioni"""
    html = """
    <html>
    <head>
        <title>Gestionale Autolavaggio</title>
        <meta charset="utf-8">
        <style>
            body { 
                font-family: Arial, sans-serif; 
                margin: 40px; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .container {
                text-align: center;
                background: rgba(255,255,255,0.1);
                padding: 40px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
            }
            h1 { color: white; font-size: 2.5em; margin-bottom: 20px; }
            .success { color: #00ff88; font-size: 1.2em; margin: 20px 0; }
            .links { margin-top: 30px; }
            .links a { 
                color: white; 
                text-decoration: none; 
                background: rgba(255,255,255,0.2);
                padding: 10px 20px;
                border-radius: 25px;
                margin: 0 10px;
                display: inline-block;
                transition: all 0.3s;
            }
            .links a:hover { 
                background: rgba(255,255,255,0.3);
                transform: translateY(-2px);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚗 Gestionale Autolavaggio</h1>
            <div class="success">✅ Server Django funzionante!</div>
            <p>Il sistema è stato configurato correttamente e il server è attivo.</p>
            <div class="links">
                <a href="/admin/">🔧 Admin Panel</a>
                <a href="#" onclick="showInfo()">ℹ️ Info Sistema</a>
            </div>
            <div style="margin-top: 30px; font-size: 0.9em; opacity: 0.8;">
                <p>🎉 Setup completato con successo!</p>
                <p>Prossimi passi:</p>
                <p>1. Vai all'Admin Panel</p>
                <p>2. Crea un superuser: <code>python manage.py createsuperuser</code></p>
                <p>3. Configura i dati base del sistema</p>
            </div>
        </div>
        
        <script>
        function showInfo() {
            alert('Sistema Gestionale Autolavaggio\\n\\nVersione: 1.0\\nDjango: Installato\\nDatabase: SQLite\\nStato: Funzionante\\n\\nPer accedere alle funzionalità complete,\\nconfigura prima i dati nell\\'Admin Panel.');
        }
        </script>
    </body>
    </html>
    """
    return HttpResponse(html)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_view, name='home'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)