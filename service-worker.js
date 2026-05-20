// Rota Padel PWA — service worker
// Стратегия: cache-first с фоновым обновлением (stale-while-revalidate).
// При обновлении файлов на GitHub Pages меняй CACHE_VERSION на новое значение —
// старый кеш будет удалён при следующем заходе пользователя.

const CACHE_VERSION = 'rota-v13';

const APP_SHELL = [
  './',
  './index.html',
  './manifest.json',
  './icon.svg',
  './icon-192.png',
  './icon-512.png',
  './icon-180.png',
  './data/schedules.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle GET requests
  if (req.method !== 'GET') return;

  // Skip non-http(s) requests (e.g., chrome-extension)
  if (!req.url.startsWith('http')) return;

  event.respondWith(
    caches.match(req).then((cached) => {
      const networkFetch = fetch(req)
        .then((resp) => {
          // Cache successful, basic-type responses (same-origin) and CORS responses
          if (resp && resp.status === 200 && (resp.type === 'basic' || resp.type === 'cors')) {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
          }
          return resp;
        })
        .catch(() => cached);

      // Return cached version immediately if available, otherwise wait for network
      return cached || networkFetch;
    })
  );
});
