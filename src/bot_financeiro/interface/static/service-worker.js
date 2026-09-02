const CACHE_NAME = "bot-financeiro-shell-v1";
const APP_SHELL = [
  "/",
  "/resumo",
  "/lancamentos",
  "/contas",
  "/cartoes",
  "/planejamento",
  "/configuracoes",
  "/manifest.webmanifest",
  "/static/app.js",
  "/static/icon.svg",
  "/static/styles.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))),
  );
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== "GET" || requestUrl.origin !== self.location.origin) {
    return;
  }

  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
