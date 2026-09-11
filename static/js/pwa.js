(function () {
  "use strict";

  let registration = null;
  let activationRequested = false;
  let lastUpdateCheck = 0;
  const UPDATE_INTERVAL_MS = 5 * 60 * 1000;

  function dispatch(name, detail = {}) {
    window.dispatchEvent(new CustomEvent(name, {detail}));
  }

  function openFeatureMode() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("pwa") !== "slideshow") return;
    const slideshow = document.getElementById("slideshow");
    if (!slideshow || !window.bootstrap?.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(slideshow).show();
  }

  function showUpdateAvailable(worker) {
    if (!worker) return;
    const banner = document.getElementById("pwa-update-banner");
    if (banner) banner.hidden = false;
    dispatch("simpleoffice:pwa-update-available", {registration});
  }

  function watchRegistration(reg) {
    if (reg.waiting) showUpdateAvailable(reg.waiting);
    reg.addEventListener("updatefound", function () {
      const worker = reg.installing;
      if (!worker) return;
      worker.addEventListener("statechange", function () {
        if (worker.state === "installed" && navigator.serviceWorker.controller) {
          showUpdateAvailable(worker);
        }
      });
    });
  }

  async function checkForUpdate(force = false) {
    if (!registration) return null;
    const now = Date.now();
    if (!force && now - lastUpdateCheck < UPDATE_INTERVAL_MS) return registration;
    lastUpdateCheck = now;
    try {
      await registration.update();
    } catch (error) {
      console.warn("SimpleOffice service worker update check failed", error);
    }
    return registration;
  }

  function activateUpdate() {
    if (!registration?.waiting) return false;
    activationRequested = true;
    registration.waiting.postMessage({type: "SKIP_WAITING"});
    return true;
  }

  window.SimpleOfficePWA = {
    get registration() { return registration; },
    checkForUpdate,
    activateUpdate,
  };

  window.addEventListener("load", openFeatureMode);

  const updateButton = document.getElementById("pwa-update-apply");
  updateButton?.addEventListener("click", activateUpdate);

  if (!("serviceWorker" in navigator) || !window.isSecureContext) {
    document.documentElement.dataset.pwa = "unsupported";
    return;
  }
  document.documentElement.dataset.pwa = "supported";

  navigator.serviceWorker.addEventListener("controllerchange", function () {
    dispatch("simpleoffice:pwa-controller-change", {});
    if (activationRequested) window.location.reload();
  });

  window.addEventListener("online", function () {
    checkForUpdate(true);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") checkForUpdate(false);
  });

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/service-worker.js", {
      scope: "/",
      updateViaCache: "none",
    }).then(function (reg) {
      registration = reg;
      watchRegistration(reg);
      dispatch("simpleoffice:pwa-ready", {registration: reg});
      return checkForUpdate(false);
    }).catch(function (error) {
      document.documentElement.dataset.pwa = "error";
      console.warn("SimpleOffice service worker could not be registered", error);
      dispatch("simpleoffice:pwa-error", {error});
    });
  });
}());
