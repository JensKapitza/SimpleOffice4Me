"use strict";

const CACHE_PREFIX = "simpleoffice-shell-";
const CACHE_NAME = `${CACHE_PREFIX}v2`;
const OFFLINE_URL = "/static/offline.html";
const SHELL_ASSETS = [
  OFFLINE_URL,
  "/static/css/app.css",
  "/static/css/quality-wins.css",
  "/static/js/theme.js",
  "/static/js/security.js",
  "/static/js/app.js",
  "/static/js/global_ui.js",
  "/static/js/pwa.js",
  "/static/icons/simpleoffice.svg"
];

self.addEventListener("install", function (event) {
  event.waitUntil((async function () {
    const cache = await caches.open(CACHE_NAME);
    // Installation is atomic: a partially cached shell must never replace a
    // previously working offline cache. Updated workers remain waiting until
    // the UI explicitly sends SKIP_WAITING.
    await Promise.all(SHELL_ASSETS.map(function (asset) {
      return cache.add(new Request(asset, {cache: "reload"}));
    }));
  }()));
});

self.addEventListener("activate", function (event) {
  event.waitUntil((async function () {
    const names = await caches.keys();
    await Promise.all(names
      .filter(function (name) { return name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME; })
      .map(function (name) { return caches.delete(name); }));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch (_error) { /* optional */ }
    }
    await self.clients.claim();
  }()));
});

function cacheable(response) {
  if (!response || !response.ok || response.type !== "basic") return false;
  const cacheControl = response.headers.get("Cache-Control") || "";
  return !/\bno-store\b/i.test(cacheControl);
}

async function networkFirstNavigation(event) {
  try {
    const preload = await event.preloadResponse;
    if (preload) return preload;
    return await fetch(event.request);
  } catch (_error) {
    return await caches.match(OFFLINE_URL) || Response.error();
  }
}

async function staleWhileRevalidate(event) {
  const request = event.request;
  const cached = await caches.match(request);
  const network = fetch(request).then(async function (response) {
    if (cacheable(response)) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(request, response.clone());
    }
    return response;
  }).catch(function () { return null; });

  if (cached) {
    event.waitUntil(network.then(function () { return undefined; }));
    return cached;
  }
  return await network || Response.error();
}

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET" || request.headers.has("range")) return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(event));
    return;
  }

  // Never persist application responses containing contacts, HR, calendar or documents.
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(staleWhileRevalidate(event));
});

self.addEventListener("message", function (event) {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});
