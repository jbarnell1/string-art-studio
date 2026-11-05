// Service worker: the shell and static files come from the cache when offline, everything else
// (pages, API, images) goes network first so a build in progress never shows stale data.
// Works at the root or under a path prefix: BASE is derived from where this file is served.
const BASE = self.location.pathname.replace(/\/sw\.js$/, "");
const VERSION = "sas-v1";
const SHELL = [
  `${BASE}/`,
  `${BASE}/static/app.css`,
  `${BASE}/static/app.js`,
  `${BASE}/static/icons/icon-192.png`,
  `${BASE}/static/icons/icon-512.png`,
  `${BASE}/manifest.webmanifest`,
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const DEV = ["localhost", "127.0.0.1"].includes(self.location.hostname);

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || DEV) return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const isStatic = url.pathname.startsWith(`${BASE}/static/`);
  if (isStatic) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy));
        return res;
      }))
    );
    return;
  }
  // Network first; the cached shell is the fallback for navigations when offline.
  event.respondWith(
    fetch(req).then((res) => {
      if (req.mode === "navigate") {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(`${BASE}/`, copy));
      }
      return res;
    }).catch(() => (req.mode === "navigate" ? caches.match(`${BASE}/`) : caches.match(req)))
  );
});
