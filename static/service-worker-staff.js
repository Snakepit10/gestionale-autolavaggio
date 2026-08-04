// Service Worker della PWA STAFF ("MasterWash Gestionale"), scope '/'.
// Convive con il SW clienti (/service-worker.js, scope /app/): questo
// bypassa tutto cio' che sta sotto /app/ e NON tocca mai le cache
// autolavaggio-* (appartengono all'altro SW, stesso origin).
// Strategie: network-first per l'HTML (dati operativi sempre freschi),
// cache-first per gli statici, fallback /offline.html.

const CACHE_NAME = 'gestionale-cache-v1';
const OFFLINE_CACHE = 'gestionale-offline-v1';

// Precache minimo (cache.addAll e' atomico: lista corta = install robusto)
const CACHE_URLS = [
    '/static/staff-manifest.json',
    '/static/icons/staff/icon-192x192.png',
    '/static/icons/staff/icon-512x512.png',
];

// CDN best-effort (non bloccano l'install)
const CDN_URLS = [
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
];

const OFFLINE_PAGE = '/offline.html';

self.addEventListener('install', (event) => {
    event.waitUntil(
        Promise.all([
            caches.open(CACHE_NAME).then((cache) => cache.addAll(CACHE_URLS)),
            caches.open(OFFLINE_CACHE).then((cache) => cache.add(OFFLINE_PAGE)),
            caches.open(CACHE_NAME).then((cache) =>
                Promise.allSettled(CDN_URLS.map((u) => cache.add(u).catch(() => null)))
            ),
        ])
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        // Elimina SOLO le vecchie versioni delle cache gestionale-*:
        // le cache autolavaggio-* sono del SW clienti e restano intatte.
        caches.keys().then((names) =>
            Promise.all(
                names.map((n) => {
                    if (n.startsWith('gestionale-') &&
                        n !== CACHE_NAME && n !== OFFLINE_CACHE) {
                        return caches.delete(n);
                    }
                })
            )
        )
    );
    self.clients.claim();
});

// Path che questo SW NON deve intercettare
const NETWORK_ONLY_PREFIXES = [
    '/app/',             // PWA clienti: autonoma, ha il suo SW
    '/admin/',           // Django admin
    '/auth/',            // login/logout
    '/accounts/',        // allauth (Google OAuth)
    '/health/',          // health check Railway
    '/monete/',          // webhook pagamenti + saldo live
    '/tasks/api/',       // conteggio badge: sempre fresco
    '/clienti/cerca/',   // autocomplete cliente in cassa
    '/turni/',           // dashboard operatore: dati live
    '/ordini/cassa',     // POS: stato carrello sempre fresco
    '/postazioni/',      // dashboard postazioni: ordini live
    '/cq/analytics',     // analytics live
    '/service-worker',   // mai cachare i SW stessi
];

const NETWORK_ONLY_PATTERNS = [
    /\/api\//,
    /\/ws\//,
    /\?.*csrf/i,
];

function shouldBypassSW(url, request) {
    if (!request.url.startsWith('http')) return true;
    if (request.method !== 'GET') return true;
    if (url.origin !== self.location.origin) return false; // CDN: decide handleFetch
    // Root '/': redirect dinamica per ruolo (HomeView), mai cacharla
    if (url.pathname === '/') return true;
    for (const p of NETWORK_ONLY_PREFIXES) {
        if (url.pathname.startsWith(p)) return true;
    }
    for (const re of NETWORK_ONLY_PATTERNS) {
        if (re.test(url.pathname) || re.test(url.search)) return true;
    }
    return false;
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);
    if (shouldBypassSW(url, request)) return;
    event.respondWith(handleFetch(request));
});

async function handleFetch(request) {
    try {
        if (isStaticResource(request)) {
            return await cacheFirst(request);
        }
        // HTML e tutto il resto: network-first (dati gestionale freschi)
        return await networkFirst(request);
    } catch (error) {
        return await handleOffline(request);
    }
}

async function cacheFirst(request) {
    const cached = await caches.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, response.clone());
    }
    return response;
}

async function networkFirst(request) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await caches.match(request);
        if (cached) return cached;
        throw error;
    }
}

async function handleOffline(request) {
    if (request.headers.get('accept')?.includes('text/html')) {
        const offline = await caches.match(OFFLINE_PAGE);
        if (offline) return offline;
    }
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(
        JSON.stringify({ error: 'Contenuto non disponibile offline', offline: true }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
}

function isStaticResource(request) {
    const url = new URL(request.url);
    const exts = ['.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg',
                  '.ico', '.woff', '.woff2'];
    return exts.some((e) => url.pathname.endsWith(e)) ||
           url.pathname.startsWith('/static/') ||
           url.hostname.includes('cdn.');
}

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    if (event.data && event.data.type === 'GET_VERSION') {
        event.ports[0].postMessage({ version: CACHE_NAME });
    }
});

console.log('Service Worker Gestionale: pronto (', CACHE_NAME, ')');
