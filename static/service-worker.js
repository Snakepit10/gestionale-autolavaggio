// Service Worker per PWA Autolavaggio
const CACHE_NAME = 'autolavaggio-cache-v1';
const OFFLINE_CACHE = 'autolavaggio-offline-v1';

// File da cachare per il funzionamento offline
const CACHE_URLS = [
    '/',
    '/ordini/cassa/mobile/',
    '/static/manifest.json',
    '/static/css/bootstrap.min.css',
    '/static/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css'
];

// Pagina offline fallback
const OFFLINE_PAGE = '/offline.html';

// Installazione del Service Worker
self.addEventListener('install', (event) => {
    console.log('Service Worker: Installazione in corso...');
    
    event.waitUntil(
        Promise.all([
            // Cache principale
            caches.open(CACHE_NAME).then((cache) => {
                console.log('Service Worker: Cache principale aperta');
                return cache.addAll(CACHE_URLS);
            }),
            // Cache offline
            caches.open(OFFLINE_CACHE).then((cache) => {
                console.log('Service Worker: Cache offline aperta');
                return cache.add(OFFLINE_PAGE);
            })
        ])
    );
    
    // Forza l'attivazione del nuovo service worker
    self.skipWaiting();
});

// Attivazione del Service Worker
self.addEventListener('activate', (event) => {
    console.log('Service Worker: Attivazione in corso...');
    
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    // Elimina cache vecchie
                    if (cacheName !== CACHE_NAME && cacheName !== OFFLINE_CACHE) {
                        console.log('Service Worker: Eliminazione cache vecchia:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    
    // Prendi il controllo di tutte le pagine
    self.clients.claim();
});

// Intercettazione delle richieste
self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);
    
    // Gestisci solo richieste HTTP/HTTPS
    if (request.url.startsWith('http')) {
        event.respondWith(handleFetch(request));
    }
});

async function handleFetch(request) {
    const url = new URL(request.url);
    
    try {
        // Strategia: Network First per le API
        if (url.pathname.startsWith('/api/')) {
            return await networkFirst(request);
        }
        
        // Strategia: Cache First per risorse statiche
        if (isStaticResource(request)) {
            return await cacheFirst(request);
        }
        
        // Strategia: Stale While Revalidate per pagine HTML
        if (request.headers.get('accept')?.includes('text/html')) {
            return await staleWhileRevalidate(request);
        }
        
        // Default: Network First
        return await networkFirst(request);
        
    } catch (error) {
        console.error('Service Worker: Errore nel fetch:', error);
        return await handleOffline(request);
    }
}

// Strategia Cache First (per risorse statiche)
async function cacheFirst(request) {
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
        return cachedResponse;
    }
    
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
}

// Strategia Network First (per dati dinamici)
async function networkFirst(request) {
    try {
        const networkResponse = await fetch(request);
        
        if (networkResponse.ok) {
            // Cache la risposta per uso futuro offline
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, networkResponse.clone());
        }
        
        return networkResponse;
    } catch (error) {
        // Se la rete fallisce, usa la cache
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }
        throw error;
    }
}

// Strategia Stale While Revalidate (per pagine HTML)
async function staleWhileRevalidate(request) {
    const cachedResponse = await caches.match(request);
    
    // Fetch in background per aggiornare la cache
    const fetchPromise = fetch(request).then((networkResponse) => {
        if (networkResponse.ok) {
            const cache = caches.open(CACHE_NAME);
            cache.then(c => c.put(request, networkResponse.clone()));
        }
        return networkResponse;
    }).catch(() => {
        // Ignora errori di rete in background
    });
    
    // Restituisci immediatamente la versione cached se disponibile
    if (cachedResponse) {
        return cachedResponse;
    }
    
    // Altrimenti aspetta la rete
    return await fetchPromise;
}

// Gestione richieste offline
async function handleOffline(request) {
    // Per richieste HTML, mostra la pagina offline
    if (request.headers.get('accept')?.includes('text/html')) {
        const offlinePage = await caches.match(OFFLINE_PAGE);
        if (offlinePage) {
            return offlinePage;
        }
    }
    
    // Per altre richieste, cerca nella cache
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
        return cachedResponse;
    }
    
    // Ultima risorsa: risposta di errore
    return new Response(
        JSON.stringify({
            error: 'Contenuto non disponibile offline',
            offline: true
        }),
        {
            status: 503,
            headers: {
                'Content-Type': 'application/json'
            }
        }
    );
}

// Determina se una richiesta è per una risorsa statica
function isStaticResource(request) {
    const url = new URL(request.url);
    const staticExtensions = ['.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2'];
    
    return staticExtensions.some(ext => url.pathname.endsWith(ext)) ||
           url.pathname.startsWith('/static/') ||
           url.hostname.includes('cdn.');
}

// Gestione messaggi dal client
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    
    if (event.data && event.data.type === 'GET_VERSION') {
        event.ports[0].postMessage({
            version: CACHE_NAME
        });
    }
    
    if (event.data && event.data.type === 'CACHE_URLS') {
        const urls = event.data.urls;
        caches.open(CACHE_NAME).then(cache => {
            cache.addAll(urls);
        });
    }
});

// Gestione aggiornamenti in background
self.addEventListener('backgroundsync', (event) => {
    if (event.tag === 'background-sync') {
        event.waitUntil(doBackgroundSync());
    }
});

async function doBackgroundSync() {
    // Sincronizza dati offline quando la connessione ritorna
    console.log('Service Worker: Sincronizzazione in background...');
    
    try {
        // Recupera ordini salvati offline
        const offlineOrders = await getOfflineOrders();
        
        for (const order of offlineOrders) {
            try {
                await submitOrder(order);
                await removeOfflineOrder(order.id);
            } catch (error) {
                console.error('Service Worker: Errore sync ordine:', error);
            }
        }
    } catch (error) {
        console.error('Service Worker: Errore background sync:', error);
    }
}

// Funzioni helper per gestione dati offline
async function getOfflineOrders() {
    // Implementa logica per recuperare ordini salvati offline
    return [];
}

async function submitOrder(order) {
    // Implementa logica per inviare ordine al server
    const response = await fetch('/api/ordini/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(order)
    });
    
    if (!response.ok) {
        throw new Error('Errore invio ordine');
    }
    
    return response.json();
}

async function removeOfflineOrder(orderId) {
    // Implementa logica per rimuovere ordine dalle cache offline
    console.log('Service Worker: Ordine sincronizzato e rimosso:', orderId);
}

// Log per debug
console.log('Service Worker: Caricato e pronto!');