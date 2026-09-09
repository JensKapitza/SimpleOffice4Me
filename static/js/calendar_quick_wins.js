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

    const icon = (className) => {
      const element = document.createElement("i");
      element.className = className;
      element.setAttribute("aria-hidden", "true");
      return element;
    };

    // Every manually created/edited appointment must expose its context in the title.
    // The explicit prefix also survives ICS/CalDAV clients that do not know our metadata.
    const normalizeAppointmentTitle = (title, kind, company) => {
      const clean = String(title || "").replace(/^\[(?:PRIVAT|GESCHÄFTLICH)\]\s*/i, "").trim();
      const firm = String(company || "").trim();
      if (kind === "business") return `[GESCHÄFTLICH] ${clean}${firm ? ` – ${firm}` : ""}`;
      return `[PRIVAT] ${clean}`;
    };
    document.querySelectorAll('form[action$="/documents/calendar"], form[action*="/documents/calendar/"]').forEach((form) => {
      const title = form.querySelector('[name="title"]');
      if (!title || form.dataset.appointmentContextReady) return;
      form.dataset.appointmentContextReady = "1";
      const row = document.createElement("div");
      row.className = "row g-2 mb-2";
      const kindColumn = document.createElement("div");
      kindColumn.className = "col-md-4";
      const kindLabel = document.createElement("label");
      kindLabel.className = "form-label";
      kindLabel.textContent = "Grundtyp";
      const kindSelect = document.createElement("select");
      kindSelect.className = "form-select";
      kindSelect.dataset.appointmentContext = "";
      kindSelect.required = true;
      [["", "Bitte wählen"], ["private", "Privat"], ["business", "Geschäftlich"]].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        kindSelect.append(option);
      });
      kindColumn.append(kindLabel, kindSelect);
      const companyColumn = document.createElement("div");
      companyColumn.className = "col-md-8";
      const companyLabel = document.createElement("label");
      companyLabel.className = "form-label";
      companyLabel.append(document.createTextNode("Firma "));
      const companyHint = document.createElement("span");
      companyHint.className = "text-secondary";
      companyHint.textContent = "(bei geschäftlich, falls vorhanden)";
      companyLabel.append(companyHint);
      const companyInput = document.createElement("input");
      companyInput.className = "form-control";
      companyInput.dataset.appointmentCompany = "";
      companyInput.maxLength = 160;
      companyInput.autocomplete = "organization";
      companyInput.placeholder = "Firma / Organisation";
      companyColumn.append(companyLabel, companyInput);
      row.append(kindColumn, companyColumn);
      title.closest(".col-md-6, .col-md, .mb-2, .mb-3")?.before(row) || title.before(row);
      const kind = row.querySelector("[data-appointment-context]");
      const company = row.querySelector("[data-appointment-company]");
      const contact = form.querySelector('[name="contact_id"]');
      const prefix = title.value.match(/^\[(PRIVAT|GESCHÄFTLICH)\]\s*/i)?.[1]?.toUpperCase();
      if (prefix) kind.value = prefix === "PRIVAT" ? "private" : "business";
      const companyMatch = title.value.match(/^\[GESCHÄFTLICH\]\s*.*?\s[–-]\s(.+)$/i);
      if (companyMatch) company.value = companyMatch[1].trim();
      const syncCompanyState = () => {
        company.disabled = kind.value !== "business";
        if (kind.value === "business" && !company.value.trim() && contact?.selectedOptions?.[0]?.value) {
          company.placeholder = `z. B. ${contact.selectedOptions[0].textContent.trim()}`;
        }
      };
      kind.addEventListener("change", syncCompanyState);
      contact?.addEventListener("change", syncCompanyState);
      syncCompanyState();
      form.addEventListener("submit", (event) => {
        if (!kind.value) {
          event.preventDefault();
          kind.setCustomValidity("Privat oder Geschäftlich muss ausgewählt werden.");
          kind.reportValidity();
          return;
        }
        kind.setCustomValidity("");
        title.value = normalizeAppointmentTitle(title.value, kind.value, company.value);
      });
    });

    const reminderCount = Number(reminders?.querySelector(".card-header .badge")?.textContent.trim() || 0);
    const pendingInvitations = scheduling ? Array.from(scheduling.querySelectorAll(".badge")).filter((badge) => badge.textContent.trim() === "pending").length : 0;
    const schedulingEnabled = schedulingAccess?.querySelector(".card-header .badge")?.textContent.trim() === "aktiv";
    const googleReady = google?.querySelector(".card-header .badge")?.textContent.trim() === "Bereit";
    const cockpit = document.createElement("section");
    cockpit.className = "card mb-4"; cockpit.id = "calendar-cockpit";
    const cockpitHeader = document.createElement("div");
    cockpitHeader.className = "card-header d-flex flex-wrap justify-content-between align-items-center gap-2";
    const cockpitTitle = document.createElement("strong");
    cockpitTitle.textContent = "Kalendercockpit";
    const viewButtons = document.createElement("div");
    viewButtons.className = "btn-group btn-group-sm";
    viewButtons.setAttribute("role", "group");
    viewButtons.setAttribute("aria-label", "Kalenderansicht");
    [["calendar-view-essential", "Kompakt"], ["calendar-view-all", "Alles anzeigen"]].forEach(([id, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-outline-secondary";
      button.id = id;
      button.textContent = label;
      viewButtons.append(button);
    });
    cockpitHeader.append(cockpitTitle, viewButtons);
    const cockpitBody = document.createElement("div");
    cockpitBody.className = "card-body";
    const metrics = document.createElement("div");
    metrics.className = "row g-2 text-center";
    [[reminderCount, "Erinnerungen"], [pendingInvitations, "Offene Einladungen"], [schedulingEnabled ? "Ja" : "Nein", "Scheduling aktiv"], [googleReady ? "Bereit" : "Aus", "Google Sync"]].forEach(([value, label]) => {
      const column = document.createElement("div");
      column.className = "col-6 col-lg-3";
      const box = document.createElement("div");
      box.className = "border rounded p-2";
      const metric = document.createElement("div");
      metric.className = "fs-5 fw-semibold";
      metric.textContent = String(value);
      const caption = document.createElement("div");
      caption.className = "small text-secondary";
      caption.textContent = label;
      box.append(metric, caption);
      column.append(box);
      metrics.append(column);
    });
    cockpitBody.append(metrics);
    cockpit.append(cockpitHeader, cockpitBody);
    if (topRow) topRow.after(cockpit);
    const targets = [["reminders", "Erinnerungen"], ["scheduling", "Einladungen"], ["scheduling-access", "Verfügbarkeit"], ["caldav", "CalDAV"], ["google-calendar-sync", "Google Sync"]].filter(([id]) => document.getElementById(id));
    const navigation = document.createElement("nav"); navigation.className = "d-flex flex-wrap gap-2 mb-4"; navigation.setAttribute("aria-label", "Kalender-Schnellnavigation");
    targets.forEach(([id, label]) => { const link = document.createElement("a"); link.className = "btn btn-sm btn-outline-primary"; link.href = `#${id}`; link.textContent = label; navigation.append(link); });
    cockpit.after(navigation);
    if (reminders) {
      const items = Array.from(reminders.querySelectorAll(".list-group > .list-group-item"));
      if (items.length && !items.every((item) => item.classList.contains("text-secondary"))) {
        const filter = document.createElement("div"); filter.className = "input-group input-group-sm mb-3";
        const iconBox = document.createElement("span");
        iconBox.className = "input-group-text";
        iconBox.append(icon("fas fa-filter"));
        const input = document.createElement("input");
        input.type = "search";
        input.className = "form-control";
        input.placeholder = "Erinnerungen filtern …";
        input.setAttribute("aria-label", "Erinnerungen filtern");
        const count = document.createElement("span");
        count.className = "input-group-text";
        count.dataset.calendarReminderCount = "";
        count.textContent = String(items.length);
        filter.append(iconBox, input, count);
        const list = reminders.querySelector(".list-group"); list?.before(filter);
        input.addEventListener("input", () => { const query = input.value.trim().toLowerCase(); let visible = 0; items.forEach((item) => { const show = !query || item.textContent.toLowerCase().includes(query); item.classList.toggle("d-none", !show); if (show) visible += 1; }); count.textContent = `${visible}/${items.length}`; });
      }
    }
    [schedulingAccess, caldav].filter(Boolean).forEach((section) => { section.querySelectorAll("code").forEach((code) => { const text = code.textContent.trim(); if (!text || code.nextElementSibling?.matches("[data-copy-calendar-value]")) return; const button = document.createElement("button"); button.type = "button"; button.className = "btn btn-sm btn-link py-0 px-1 align-baseline"; button.dataset.copyCalendarValue = "1"; button.title = "In die Zwischenablage kopieren"; button.setAttribute("aria-label", `${text} kopieren`); button.append(icon("fas fa-copy")); code.after(button); button.addEventListener("click", async () => { try { await navigator.clipboard.writeText(text); button.replaceChildren(icon("fas fa-check")); setTimeout(() => { button.replaceChildren(icon("fas fa-copy")); }, 1200); } catch (error) { console.warn("Kalenderadresse konnte nicht kopiert werden", error); } }); }); });
    const advancedSections = [google, schedulingAccess, caldav].filter(Boolean); const storageKey = "simpleoffice.calendar.compactView";
    const applyCompact = (compact) => { advancedSections.forEach((section) => section.classList.toggle("d-none", compact)); cockpit.querySelector("#calendar-view-essential")?.classList.toggle("active", compact); cockpit.querySelector("#calendar-view-all")?.classList.toggle("active", !compact); try { localStorage.setItem(storageKey, compact ? "1" : "0"); } catch (_) {} };
    let compact = false; try { compact = localStorage.getItem(storageKey) === "1"; } catch (_) {} applyCompact(compact);
    cockpit.querySelector("#calendar-view-essential")?.addEventListener("click", () => applyCompact(true)); cockpit.querySelector("#calendar-view-all")?.addEventListener("click", () => applyCompact(false));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initCalendarQuickWins); else initCalendarQuickWins();
})();