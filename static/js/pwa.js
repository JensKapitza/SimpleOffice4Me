(function () {
  "use strict";

  function openFeatureMode() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("pwa") !== "slideshow") {
      return;
    }
    const slideshow = document.getElementById("slideshow");
    if (!slideshow || !window.bootstrap || !window.bootstrap.Modal) {
      return;
    }
    window.bootstrap.Modal.getOrCreateInstance(slideshow).show();
  }

  window.addEventListener("load", openFeatureMode);

  if (!("serviceWorker" in navigator) || !window.isSecureContext) {
    return;
  }

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/service-worker.js", {scope: "/"})
      .catch(function (error) {
        console.warn("SimpleOffice service worker could not be registered", error);
      });
  });
}());
