// Service Worker per PWA Autolavaggio
// Ascolta messaggi dal client (es. SKIP_WAITING via update toast)
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});

const CACHE_NAME = 'autolavaggio-cache-v7';
const OFFLINE_CACHE = 'autolavaggio-offline-v7';

// File essenziali da pre-cachare per funzionamento offline
// (cache.addAll e' atomico: se UNO fallisce, tutto fallisce)
// NOTA: '/' NON va precached perche e' una redirect dinamica (HomeView
// dispatch in base al ruolo dell'utente). Va sempre alla rete.
const CACHE_URLS = [
    '/static/manifest.json',
    '/static/css/style.css',
    '/static/js/app.js',
    '/static/icons/icon-192x192.png',
    '/static/icons/icon-512x512.png',
];

// Asset CDN: pre-fetch best-effort (non bloccano l'install)
const CDN_URLS = [
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css',
];

// Pagina offline fallback
const OFFLINE_PAGE = '/offline.html';

// Installazione del Service Worker
self.addEventListener('install', (event) => {
    console.log('Service Worker: Installazione in corso...');
    
    event.waitUntil(
        Promise.all([
            // Cache principale (atomica: addAll fallisce se uno solo fallisce)
            caches.open(CACHE_NAME).then((cache) => cache.addAll(CACHE_URLS)),
            // Cache offline page
            caches.open(OFFLINE_CACHE).then((cache) => cache.add(OFFLINE_PAGE)),
            // CDN best-effort: ogni URL e' add() singolo, fallimenti non bloccano install
            caches.open(CACHE_NAME).then((cache) =>
                Promise.allSettled(CDN_URLS.map((u) => cache.add(u).catch(() => null)))
            ),
        ])
    );

    // Forza l'attivazione del nuovo service worker
    self.skipWaiting();
});

// Attivazione del Service Worker
self.addEventListener('activate', (event) => {
    console.log('Service Worker: Attivazione in corso...');

    event.waitUntil(
        Promise.all([
            // 1. Elimina cache vecchie con CACHE_NAME diverso
            caches.keys().then((cacheNames) =>
                Promise.all(
                    cacheNames.map((cacheName) => {
                        if (cacheName !== CACHE_NAME && cacheName !== OFFLINE_CACHE) {
                            console.log('SW: elimina cache vecchia:', cacheName);
                            return caches.delete(cacheName);
                        }
                    })
                )
            ),
            // 2. Difensivo: pulisci eventuali entry residue di '/' nelle
            //    cache attuali (per chi aveva v3/v4/v5 con root precached).
            caches.open(CACHE_NAME).then((cache) =>
                cache.delete('/').catch(() => null)
            ),
        ])
    );

    self.clients.claim();
});

// Intercettazione delle richieste
// Path che il SW NON deve intercettare (network-only o lasciate al browser)
const NETWORK_ONLY_PREFIXES = [
    '/admin/',           // Django admin (sessione cookie)
    '/auth/',            // login/logout
    '/health/',          // health check Railway
    '/clienti/cerca/',   // autocomplete cliente in cassa
    '/turni/',           // dashboard operatore: dati live (coda, completate)
    '/ordini/cassa',     // POS: stato carrello sempre fresco
    '/postazioni/',      // dashboard postazioni: ordini live
    '/cq/analytics',     // analytics KPI: dati live (segnalazioni in tempo reale)
];

const NETWORK_ONLY_PATTERNS = [
    /\/api\//,           // tutte le API (dynamic)
    /\/ws\//,            // WebSocket upgrade
    /\?.*csrf/i,         // qualsiasi query con csrf
];

function shouldBypassSW(url, request) {
    // Bypass: protocolli non http(s) e WebSocket
    if (!request.url.startsWith('http')) return true;
    // Bypass: metodi non-GET (POST/PUT/DELETE non vanno cached)
    if (request.method !== 'GET') return true;
    // Bypass: cross-origin (CDN bootstrap, ecc.) — gestito comunque dal browser
    if (url.origin !== self.location.origin) return false; // lascia handleFetch decidere
    // HARD GUARD: SW e' scope /app/ ma alcuni browser potrebbero ancora
    // farci passare richieste fuori scope. Bypass tutto cio' che non e'
    // sotto /app/ o asset statici essenziali per /app/.
    if (!url.pathname.startsWith('/app/') &&
        !url.pathname.startsWith('/static/') &&
        url.pathname !== '/offline.html') {
        return true;
    }
    // Bypass: root '/' (redirect dinamica HomeView, mai precachare)
    if (url.pathname === '/') return true;
    // Bypass: prefissi
    for (const p of NETWORK_ONLY_PREFIXES) {
        if (url.pathname.startsWith(p)) return true;
    }
    // Bypass: pattern API/WS
    for (const re of NETWORK_ONLY_PATTERNS) {
        if (re.test(url.pathname) || re.test(url.search)) return true;
    }
    return false;
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);

    if (shouldBypassSW(url, request)) {
        // Lascia al browser senza intercettazione
        return;
    }

    event.respondWith(handleFetch(request));
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
            // IMPORTANTE: clonare SUBITO, sincronamente, prima che il body
            // venga consumato dal chiamante che riceve networkResponse.
            // Una .clone() in una .then() annidata fallirebbe con
            // 'Response body is already used'.
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME)
                .then(c => c.put(request, responseToCache))
                .catch(() => { /* ignora errori cache */ });
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