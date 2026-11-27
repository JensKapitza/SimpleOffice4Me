(() => {
  "use strict";
  const page = document.querySelector("[data-optional-setup]");
  if (!page) return;
  const active = new Set(["queued", "downloading", "cancelling"]);
  const snapshot = {};
  const features = ["voice-package", "kids-hand", "english-voice"];
  for (const name of features) {
    const badge = document.getElementById(name)?.querySelector("[data-optional-status]");
    if (badge && active.has(badge.dataset.optionalStatus)) snapshot[name] = badge.dataset.optionalStatus;
  }
  if (!Object.keys(snapshot).length) return;

  const english = document.documentElement.lang.toLowerCase().startsWith("en");
  const formatBytes = (value) => {
    if (!Number.isSafeInteger(value) || value < 0) return "";
    const units = ["B", "KiB", "MiB", "GiB"];
    let amount = value;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${amount.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
  };
  let stopped = false;
  window.addEventListener("pagehide", () => { stopped = true; }, {once:true});
  const deadline = Date.now() + 310000;
  const refresh = async () => {
    if (stopped || Date.now() > deadline) return;
    if (!document.hidden) {
      try {
        const response = await fetch(page.dataset.progressDetails, {
          cache:"no-store", credentials:"same-origin", redirect:"error", signal:AbortSignal.timeout(3500),
        });
        if (response.ok) {
          const details = await response.json();
          if (stopped) return;
          for (const name of features) {
            const job = details[name];
            if (!job) continue;
            const container = document.querySelector(`[data-component-progress="${name}"]`);
            const bar = container?.querySelector(".progress");
            const fill = container?.querySelector(".progress-bar");
            const message = container?.querySelector("[data-progress-message]");
            if (!container || !bar || !fill || !message) continue;
            if (active.has(job.state)) {
              container.hidden = false;
              if (Number.isInteger(job.percent) && job.percent >= 0 && job.percent <= 100) {
                fill.style.width = `${job.percent}%`;
                bar.setAttribute("aria-valuenow", String(job.percent));
                message.textContent = `${job.file || (english ? "Preparing files" : "Dateien werden vorbereitet")} · ${job.percent}% (${formatBytes(job.received)} / ${formatBytes(job.total)})`;
              } else {
                fill.style.width = "100%";
                fill.classList.add("progress-bar-striped", "progress-bar-animated");
                bar.removeAttribute("aria-valuenow");
                message.textContent = `${job.file || (english ? "Preparing files" : "Dateien werden vorbereitet")} · ${formatBytes(job.received)} ${english ? "downloaded" : "geladen"}`;
              }
              if (job.state === "cancelling") message.textContent = english ? "Cancellation requested…" : "Abbruch angefordert …";
            }
            if (snapshot[name] && !active.has(job.state)) {
              window.location.reload();
              return;
            }
          }
        }
      } catch (_error) {
        // Keep the last measured progress and manual refresh link on a poor connection.
      }
    }
    if (!stopped) window.setTimeout(refresh, 1500);
  };
  window.setTimeout(refresh, 250);
})();
