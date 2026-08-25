(() => {
  "use strict";

  function initCalendarQuickWins() {
    const main = document.querySelector("main.container-xl");
    const heading = main?.querySelector("h1");
    if (!main || heading?.textContent.trim() !== "Kalender") return;

    const topRow = heading.closest(".d-flex") || heading.parentElement;
    const reminders = document.getElementById("reminders");
    const scheduling = document.getElementById("scheduling");
    const schedulingAccess = document.getElementById("scheduling-access");
    const caldav = document.getElementById("caldav");
    const google = document.getElementById("google-calendar-sync");

    // 1) Kalendercockpit: vorhandene Zustände kompakt zusammenfassen.
    const reminderCount = Number(reminders?.querySelector(".card-header .badge")?.textContent.trim() || 0);
    const pendingInvitations = scheduling
      ? Array.from(scheduling.querySelectorAll(".badge")).filter((badge) => badge.textContent.trim() === "pending").length
      : 0;
    const schedulingEnabled = schedulingAccess?.querySelector(".card-header .badge")?.textContent.trim() === "aktiv";
    const googleReady = google?.querySelector(".card-header .badge")?.textContent.trim() === "Bereit";

    const cockpit = document.createElement("section");
    cockpit.className = "card mb-4";
    cockpit.id = "calendar-cockpit";
    cockpit.innerHTML = `
      <div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">
        <strong>Kalendercockpit</strong>
        <div class="btn-group btn-group-sm" role="group" aria-label="Kalenderansicht">
          <button type="button" class="btn btn-outline-secondary" id="calendar-view-essential">Kompakt</button>
          <button type="button" class="btn btn-outline-secondary" id="calendar-view-all">Alles anzeigen</button>
        </div>
      </div>
      <div class="card-body">
        <div class="row g-2 text-center">
          <div class="col-6 col-lg-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${reminderCount}</div><div class="small text-secondary">Erinnerungen</div></div></div>
          <div class="col-6 col-lg-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${pendingInvitations}</div><div class="small text-secondary">Offene Einladungen</div></div></div>
          <div class="col-6 col-lg-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${schedulingEnabled ? "Ja" : "Nein"}</div><div class="small text-secondary">Scheduling aktiv</div></div></div>
          <div class="col-6 col-lg-3"><div class="border rounded p-2"><div class="fs-5 fw-semibold">${googleReady ? "Bereit" : "Aus"}</div><div class="small text-secondary">Google Sync</div></div></div>
        </div>
      </div>`;
    if (topRow) topRow.after(cockpit);

    // 2) Schnellnavigation zu den großen Kalenderbereichen.
    const targets = [
      ["reminders", "Erinnerungen"],
      ["scheduling", "Einladungen"],
      ["scheduling-access", "Verfügbarkeit"],
      ["caldav", "CalDAV"],
      ["google-calendar-sync", "Google Sync"]
    ].filter(([id]) => document.getElementById(id));
    const navigation = document.createElement("nav");
    navigation.className = "d-flex flex-wrap gap-2 mb-4";
    navigation.setAttribute("aria-label", "Kalender-Schnellnavigation");
    targets.forEach(([id, label]) => {
      const link = document.createElement("a");
      link.className = "btn btn-sm btn-outline-primary";
      link.href = `#${id}`;
      link.textContent = label;
      navigation.append(link);
    });
    cockpit.after(navigation);

    // 3) Erinnerungen lokal filtern, ohne neuen Serveraufruf.
    if (reminders) {
      const items = Array.from(reminders.querySelectorAll(".list-group > .list-group-item"));
      if (items.length && !items.every((item) => item.classList.contains("text-secondary"))) {
        const filter = document.createElement("div");
        filter.className = "input-group input-group-sm mb-3";
        filter.innerHTML = `
          <span class="input-group-text"><i class="fas fa-filter" aria-hidden="true"></i></span>
          <input type="search" class="form-control" placeholder="Erinnerungen filtern …" aria-label="Erinnerungen filtern">
          <span class="input-group-text" data-calendar-reminder-count>${items.length}</span>`;
        const list = reminders.querySelector(".list-group");
        list?.before(filter);
        const input = filter.querySelector("input");
        const count = filter.querySelector("[data-calendar-reminder-count]");
        input.addEventListener("input", () => {
          const query = input.value.trim().toLowerCase();
          let visible = 0;
          items.forEach((item) => {
            const show = !query || item.textContent.toLowerCase().includes(query);
            item.classList.toggle("d-none", !show);
            if (show) visible += 1;
          });
          count.textContent = `${visible}/${items.length}`;
        });
      }
    }

    // 4) CalDAV-/Scheduling-Adressen per Klick kopieren.
    [schedulingAccess, caldav].filter(Boolean).forEach((section) => {
      section.querySelectorAll("code").forEach((code) => {
        const text = code.textContent.trim();
        if (!text || code.nextElementSibling?.matches("[data-copy-calendar-value]")) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-sm btn-link py-0 px-1 align-baseline";
        button.dataset.copyCalendarValue = "1";
        button.title = "In die Zwischenablage kopieren";
        button.setAttribute("aria-label", `${text} kopieren`);
        button.innerHTML = '<i class="fas fa-copy" aria-hidden="true"></i>';
        code.after(button);
        button.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(text);
            button.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
            setTimeout(() => { button.innerHTML = '<i class="fas fa-copy" aria-hidden="true"></i>'; }, 1200);
          } catch (error) {
            console.warn("Kalenderadresse konnte nicht kopiert werden", error);
          }
        });
      });
    });

    // 5) Kompakte Kalenderansicht merken: selten benötigte Integrationsbereiche ausblenden.
    const advancedSections = [google, schedulingAccess, caldav].filter(Boolean);
    const storageKey = "simpleoffice.calendar.compactView";
    const applyCompact = (compact) => {
      advancedSections.forEach((section) => section.classList.toggle("d-none", compact));
      cockpit.querySelector("#calendar-view-essential")?.classList.toggle("active", compact);
      cockpit.querySelector("#calendar-view-all")?.classList.toggle("active", !compact);
      try { localStorage.setItem(storageKey, compact ? "1" : "0"); } catch (_) { /* optional */ }
    };
    let compact = false;
    try { compact = localStorage.getItem(storageKey) === "1"; } catch (_) { /* optional */ }
    applyCompact(compact);
    cockpit.querySelector("#calendar-view-essential")?.addEventListener("click", () => applyCompact(true));
    cockpit.querySelector("#calendar-view-all")?.addEventListener("click", () => applyCompact(false));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCalendarQuickWins);
  } else {
    initCalendarQuickWins();
  }
})();
