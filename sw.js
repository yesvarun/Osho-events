// Minimal service worker — required so the browser offers "Install app".
// It provides a fetch handler (Chrome's installability requirement) and a tiny
// offline cache for the app shell. Safe to replace any existing sw.js.

const CACHE = "osho-camps-v1";
const SHELL = ["/", "/index.html", "/manifest.json", "/icon-192-1.png", "/icon-512-1.png"];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first for everything (so live events.json is always fresh),
// falling back to cache when offline. The presence of this fetch handler is
// what makes the site installable.
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        // optionally cache shell files
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});
