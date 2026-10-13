/* Cafe POS Service Worker */
const CACHE_NAME = 'cafe-pos-static-v1';
const OFFLINE_URL = '/offline/';

const PRECACHE_ASSETS = [
    OFFLINE_URL,
    '/manifest.webmanifest',
    '/static/pos/billing.css',
    '/static/pos/receipt.css',
    '/static/pos/billing.js',
    '/static/pos/icons/icon-192.png',
    '/static/pos/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(PRECACHE_ASSETS).catch((err) => {
                console.warn('Pre-cache warning:', err);
            });
        }).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((name) => {
                    if (name !== CACHE_NAME) {
                        return caches.delete(name);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;

    // RULE 1: Never cache non-GET requests (e.g. POST, PUT, DELETE)
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // RULE 2: HTML navigation requests - NEVER cache authenticated HTML pages
    // Always fetch from network. If offline, return the offline fallback page.
    if (request.mode === 'navigate' || (request.headers.get('accept') && request.headers.get('accept').includes('text/html'))) {
        event.respondWith(
            fetch(request).catch(() => {
                return caches.match(OFFLINE_URL);
            })
        );
        return;
    }

    // RULE 3: Static assets cache-first / network-fallback
    if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/pos/static/')) {
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                if (cachedResponse) {
                    return cachedResponse;
                }
                return fetch(request).then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseToCache = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(request, responseToCache);
                        });
                    }
                    return networkResponse;
                }).catch(() => {
                    // Fallback for static if not available
                    return new Response('', { status: 408, statusText: 'Request timed out' });
                });
            })
        );
        return;
    }

    // RULE 4: For any other GET request (API, data, etc.), fetch directly from network without caching
    event.respondWith(
        fetch(request).catch(() => {
            return new Response('', { status: 503, statusText: 'Offline' });
        })
    );
});

