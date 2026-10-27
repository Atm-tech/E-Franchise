const CACHE = 'aapoorti-shell-v17';
const SHELL = ['/static/offline.html', '/static/style.css?v=51', '/static/app-shell.js?v=14', '/static/vendor/morphdom-2.7.8.js', '/static/ui-sync.js?v=1', '/static/live-events.js?v=3', '/static/aapoorti-logo.svg', '/static/app-icon.svg', '/static/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/static/offline.html'))); return;
  }
  if (url.pathname.startsWith('/static/')) event.respondWith(fetch(event.request).then(response => {
    if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
    return response;
  }).catch(() => caches.match(event.request)));
});
