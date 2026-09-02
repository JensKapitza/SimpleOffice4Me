"use strict";

const CACHE_NAME = "simpleoffice-shell-v1";
const OFFLINE_URL = "/static/offline.html";
const SHELL_ASSETS = [
  OFFLINE_URL,
  "/static/css/app.css",
  "/static/js/theme.js",
  "/static/icons/simpleoffice.svg"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function (cache) { return cache.addAll(SHELL_ASSETS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.filter(function (name) {
          return name.startsWith("simpleoffice-shell-") && name !== CACHE_NAME;
        }).map(function (name) { return caches.delete(name); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(function () { return caches.match(OFFLINE_URL); }));
    return;
  }

  // Never persist application responses containing contacts, HR, calendar or documents.
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(
    caches.match(request).then(function (cached) {
      return cached || fetch(request);
    })
  );
});
