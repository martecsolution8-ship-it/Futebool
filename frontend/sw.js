const CACHE = "marbet-shell-v9";
const SHELL = ["/", "/styles.css", "/app.js", "/app-config.js", "/manifest.webmanifest", "/logo.png", "/favicon.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Nunca interceptar pedidos a outros domínios (ex.: marbet-api.onrender.com)
  if (url.origin !== self.location.origin) return;
  // Rede primeiro para a app e HTML (evita servir um app.js antigo do cache); cache só como reserva offline.
  event.respondWith(
    fetch(req).then(response => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(req, copy));
      }
      return response;
    }).catch(() => caches.match(req).then(cached => cached || (req.mode === "navigate" ? caches.match("/") : Response.error())))
  );
});
