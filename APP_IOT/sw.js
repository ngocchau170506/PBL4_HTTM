// Network-First Core Assets + Offline Fallback Service Worker for PI_AI Driver Cockpit PWA (APP_IOT)
const CACHE_NAME = "pi-ai-driver-pwa-v5";
const ASSETS = [
  "./driver.html",
  "./driver.css",
  "./driver_ble.js?v=9",
  "./driver_ble.js",
  "./manifest.json",
  "./icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

function isCoreAppAsset(pathname) {
  return (
    pathname.endsWith("/driver.html") ||
    pathname.endsWith("/driver_ble.js") ||
    pathname.endsWith("/driver.css") ||
    pathname.endsWith("/driver")
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  // Network-first for driver.html / driver_ble.js / driver.css so version/server switches never stick to stale cache
  if (req.mode === "navigate" || isCoreAppAsset(url.pathname)) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // Stale-while-revalidate for static icons/manifest
  event.respondWith(
    caches.match(req).then((cached) => {
      const net = fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => cached);
      return cached || net;
    })
  );
});
