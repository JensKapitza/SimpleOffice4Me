(() => {
  "use strict";
  const page = document.querySelector("[data-optional-setup]");
  if (!page) return;
  const active = new Set(["queued", "downloading"]);
  const snapshot = {};
  for (const name of ["kids-voice", "kids-hand"]) {
    const badge = document.getElementById(name)?.querySelector("[data-optional-status]");
    if (badge && active.has(badge.dataset.optionalStatus)) snapshot[name] = badge.dataset.optionalStatus;
  }
  if (!Object.keys(snapshot).length) return;
  let stopped = false;
  window.addEventListener("pagehide", () => { stopped = true; }, {once:true});
  const deadline = Date.now() + 310000;
  const refresh = async () => {
    if (stopped || Date.now() > deadline) return;
    if (!document.hidden) {
      try {
        const response = await fetch(page.dataset.progress, {
          cache:"no-store", credentials:"same-origin", redirect:"error", signal:AbortSignal.timeout(3500),
        });
        if (response.ok) {
          const current = await response.json();
          if (!stopped && Object.entries(snapshot).some(([name, status]) => current[name] !== status)) {
            window.location.reload();
            return;
          }
        }
      } catch (_error) {
        // Keep the existing status and manual refresh link on a poor connection.
      }
    }
    if (!stopped) window.setTimeout(refresh, 3000);
  };
  window.setTimeout(refresh, 3000);
})();
