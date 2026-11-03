// Minimal service worker: makes FINN installable and keeps the shell available offline.
const SHELL = "finn-shell-v1";
const ASSETS = ["/", "/static/styles.css", "/static/app.js", "/static/icon.svg", "/manifest.webmanifest"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  // network first, cache fallback — always fresh code when online
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request)));
});
