(function () {
  "use strict";

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
