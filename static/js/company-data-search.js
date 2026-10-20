(() => {
  const MAX_PASTE_CHARS = 20000;
  const MAX_FIELD_CHARS = 2000;
  const LABELS = new Map([
    ["name", "display_name"], ["company name", "display_name"],
    ["display name", "display_name"], ["company", "company"],
    ["firm", "company"], ["firma", "company"], ["firmenname", "company"],
    ["organisation", "company"], ["organization", "company"],
    ["street", "street"], ["street address", "street"],
    ["house number", "house_number"], ["housenumber", "house_number"], ["hausnummer", "house_number"],
    ["address", "street"], ["adresse", "street"], ["strasse", "street"], ["straße", "street"],
    ["postal", "postal"], ["postal code", "postal"], ["zip", "postal"],
    ["plz", "postal"], ["city", "city"], ["town", "city"], ["ort", "city"],
    ["region", "state"], ["state", "state"], ["bundesland", "state"],
    ["country", "country"], ["land", "country"],
    ["phone", "phone"], ["telephone", "phone"], ["telefon", "phone"],
    ["email", "email"], ["e mail", "email"], ["website", "website"],
    ["webseite", "website"], ["url", "website"],
  ]);

  function normalizeLabel(value) {
    return String(value || "").normalize("NFKC").trim().toLocaleLowerCase("de").replace(/[\s_-]+/g, " ");
  }

  function parseCompanyDetails(text) {
    const source = String(text || "");
    if (source.length > MAX_PASTE_CHARS) throw new RangeError("Der eingefügte Text ist zu lang.");
    const lines = source.split(/\r?\n/);
    if (lines.length > 100) throw new RangeError("Der eingefügte Text enthält zu viele Zeilen.");
    const result = {};
    for (const line of lines) {
      const separator = line.indexOf(":");
      if (separator < 1) continue;
      const target = LABELS.get(normalizeLabel(line.slice(0, separator)));
      const value = line.slice(separator + 1).trim();
      if (!target || !value) continue;
      if (value.length > MAX_FIELD_CHARS) throw new RangeError(`Der Wert für ${target} ist zu lang.`);
      if (result[target] && result[target] !== value) {
        throw new Error(`Mehrere unterschiedliche Werte für ${target}.`);
      }
      result[target] = value;
    }
    return result;
  }

  function companySearchUrls(name, city) {
    const query = [String(name || "").trim(), String(city || "").trim()].filter(Boolean).join(" ");
    const encoded = encodeURIComponent(query);
    return {
      googleMaps: `https://www.google.com/maps/search/?api=1&query=${encoded}`,
      googleSearch: `https://www.google.com/search?q=${encoded}`,
      bingMaps: `https://www.bing.com/maps?q=${encoded}`,
      openStreetMap: `https://www.openstreetmap.org/search?query=${encoded}`,
    };
  }

  function onlyEmptyContactFields(suggested, current) {
    return Object.fromEntries(
      Object.entries(suggested).filter(([key, value]) => value && !String(current[key] || "").trim()),
    );
  }

  // Keep parser/link helpers independently testable without a browser DOM.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {companySearchUrls, normalizeLabel, onlyEmptyContactFields, parseCompanyDetails};
  }

  if (typeof document === "undefined") return;
  document.querySelectorAll("[data-company-data-search]").forEach((root) => {
    const queryName = root.querySelector("[data-company-query-name]");
    const queryCity = root.querySelector("[data-company-query-city]");
    const sourceUrl = root.querySelector("[data-company-source-url]");
    const websiteValue = root.querySelector('[data-company-value="website"]');
    const websiteLink = root.querySelector("[data-company-website-link]");
    const sourceHidden = root.querySelector("[data-company-data-source]");
    const fieldsHidden = root.querySelector("[data-company-data-fields]");
    const paste = root.querySelector("[data-company-paste]");
    const status = root.querySelector("[data-company-assist-status]");
    const preview = root.querySelector("[data-company-preview]");
    const previewRows = root.querySelector("[data-company-preview-rows]");
    const duplicateResults = root.querySelector("[data-company-duplicates]");
    const endpoint = root.dataset.companySearchEndpoint;
    const contactUrl = root.dataset.contactUrlTemplate || "";
    const fields = [...root.querySelectorAll("[data-company-value]")];
    const values = new Map(fields.map((field) => [field.dataset.companyValue, field]));
    let duplicateController;
    let previewGeneration = 0;

    function updateLinks() {
      const urls = companySearchUrls(queryName.value, queryCity.value);
      for (const [key, url] of Object.entries(urls)) {
        const link = root.querySelector(`[data-company-provider="${key}"]`);
        if (!link) continue;
        link.href = url;
        link.setAttribute("aria-disabled", queryName.value.trim() ? "false" : "true");
        link.tabIndex = queryName.value.trim() ? 0 : -1;
      }
      try {
        const official = new URL(websiteValue.value.trim());
        if (!["http:", "https:"].includes(official.protocol)) throw new Error("unsupported scheme");
        websiteLink.href = official.href;
        websiteLink.hidden = false;
      } catch {
        websiteLink.hidden = true;
      }
    }

    async function checkLocalDuplicates(companyName) {
      duplicateController?.abort();
      duplicateController = new AbortController();
      const controller = duplicateController;
      duplicateResults.replaceChildren();
      if (!companyName || companyName.trim().length < 2) {
        duplicateResults.textContent = "Bitte mindestens zwei Zeichen für die lokale Dublettenprüfung eingeben.";
        return;
      }
      duplicateResults.textContent = "Lokale Kontakte werden geprüft …";
      try {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set("q", companyName.trim());
        url.searchParams.set("exclude", root.dataset.contactId || "");
        const response = await fetch(url, {
          headers: {Accept: "application/json"},
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (!response.ok) throw new Error("Local contact lookup unavailable");
        const payload = await response.json();
        duplicateResults.replaceChildren();
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!items.length) {
          duplicateResults.textContent = "Keine passenden lokalen Firmenkontakte gefunden.";
          return;
        }
        const heading = document.createElement("p");
        heading.className = "mb-1";
        heading.textContent = "Mögliche lokale Dubletten — vorhandenen Kontakt zuerst prüfen:";
        const list = document.createElement("ul");
        list.className = "mb-0";
        for (const item of items) {
          const row = document.createElement("li");
          const link = document.createElement("a");
          link.href = contactUrl.replace("__CONTACT_ID__", encodeURIComponent(item.contact_id));
          link.textContent = item.company_name || item.display_name || "Kontakt öffnen";
          row.append(link);
          if (item.email) {
            const detail = document.createElement("span");
            detail.className = "small text-secondary ms-2";
            detail.textContent = item.email;
            row.append(detail);
          }
          list.append(row);
        }
        duplicateResults.append(heading, list);
      } catch (error) {
        if (error.name === "AbortError") return;
        duplicateResults.textContent = "Lokale Dublettenprüfung nicht verfügbar; bitte Kontakte manuell prüfen.";
      }
    }

    for (const link of root.querySelectorAll("[data-company-provider], [data-company-website-link]")) {
      link.addEventListener("click", (event) => {
        if (link.matches("[data-company-provider]") && !queryName.value.trim()) {
          event.preventDefault();
          status.textContent = "Bitte zuerst einen Firmennamen für die Suche eingeben.";
          queryName.focus();
          return;
        }
        try {
          const target = new URL(link.href);
          if (target.protocol === "https:") sourceUrl.value = target.href;
        } catch { /* A disabled/invalid link is never used as provenance. */ }
      });
    }

    for (const field of [queryName, queryCity, websiteValue]) {
      field.addEventListener("input", () => {
        if (field === queryName || field === queryCity) {
          previewGeneration += 1;
          duplicateController?.abort();
        }
        updateLinks();
      });
    }
    for (const field of [queryName, queryCity]) {
      field.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        previewGeneration += 1;
        checkLocalDuplicates(queryName.value);
      });
    }
    const editForm = root.closest("form[data-company-edit-form]")
      || document.querySelector(root.dataset.companyEditForm || "[data-company-edit-form]");
    for (const field of editForm?.querySelectorAll('[name="company"], [name="display_name"]') || []) {
      field.addEventListener("input", () => {
        if (!queryName.value.trim()) queryName.value = field.value;
        updateLinks();
      });
    }
    root.querySelector("[data-company-check-duplicates]").addEventListener("click", () => {
      previewGeneration += 1;
      checkLocalDuplicates(queryName.value);
    });

    root.querySelector("[data-company-parse]").addEventListener("click", () => {
      try {
        const parsed = parseCompanyDetails(paste.value);
        for (const [key, value] of Object.entries(parsed)) values.get(key).value = value;
        status.textContent = Object.keys(parsed).length
          ? `${Object.keys(parsed).length} beschriftete Felder erkannt. Bitte Zuordnung prüfen.`
          : "Keine bekannten Feldbezeichnungen erkannt; Angaben können manuell in die Felder eingetragen werden.";
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : "Der Text konnte nicht ausgewertet werden.";
      }
    });

    root.querySelector("[data-company-preview-button]").addEventListener("click", async () => {
      const generation = ++previewGeneration;
      previewRows.replaceChildren();
      const existingAddresses = Number(root.dataset.existingAddressCount || 0);
      const suggested = Object.fromEntries(fields.map((field) => [field.dataset.companyValue, field.value.trim()]));
      const companyQuery = suggested.company || suggested.display_name || queryName.value;
      await checkLocalDuplicates(companyQuery);
      if (generation !== previewGeneration) return;
      const targetSelectors = {
        display_name: '[name="display_name"]', company: '[name="company"]',
        email: '[name="email"]', phone: '[name="phone"]', website: '[name="website"]',
      };
      const currentValues = Object.fromEntries(
        Object.entries(targetSelectors).map(([key, selector]) => [
          key, document.querySelector(`[data-company-edit-form] ${selector}`)?.value || "",
        ]),
      );
      const fillable = onlyEmptyContactFields(suggested, currentValues);
      let additions = 0;
      for (const [key, value] of Object.entries(suggested)) {
        if (!value) continue;
        const row = document.createElement("tr");
        const fieldName = document.createElement("th");
        fieldName.scope = "row";
        fieldName.textContent = values.get(key).dataset.companyLabel;
        const oldValue = document.createElement("td");
        const target = targetSelectors[key]
          ? document.querySelector(`[data-company-edit-form] ${targetSelectors[key]}`)
          : null;
        const currentValue = target ? target.value.trim() : "";
        oldValue.textContent = key === "street" || key === "city" || key === "postal" || key === "state" || key === "country"
          ? (existingAddresses ? `${existingAddresses} vorhandene Adresse(n); Vorschlag wird angefügt` : "Neue Adresse")
          : (currentValue || "—");
        const proposedValue = document.createElement("td");
        proposedValue.textContent = value;
        const action = document.createElement("td");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "form-check-input";
        checkbox.dataset.companyInclude = key;
        const addressField = ["street", "house_number", "postal", "city", "state", "country"].includes(key);
        const unchanged = currentValue && currentValue === value;
        const protectedValue = !addressField && currentValue && !fillable[key];
        checkbox.checked = !unchanged && !protectedValue;
        checkbox.disabled = Boolean(unchanged || protectedValue);
        checkbox.setAttribute("aria-label", `${values.get(key).dataset.companyLabel} übernehmen`);
        if (!checkbox.disabled) additions += 1;
        action.append(checkbox);
        row.append(fieldName, oldValue, proposedValue, action);
        previewRows.append(row);
      }
      preview.hidden = false;
      status.textContent = additions
        ? "Prüfe Feldzuordnung. Bestehende Werte bleiben unverändert; markierte Adressen werden ergänzt."
        : "Es gibt keine neuen Felder zum Übernehmen. Bestehende Werte wurden geschützt.";
    });

    root.querySelector("[data-company-apply]").addEventListener("click", () => {
      let source;
      try {
        source = new URL(sourceUrl.value.trim());
      } catch {
        status.textContent = "Bitte vor der Übernahme eine HTTPS-Quelle angeben.";
        sourceUrl.focus();
        return;
      }
      if (source.protocol !== "https:" || !source.hostname) {
        status.textContent = "Die Datenquelle muss eine gültige HTTPS-Adresse sein.";
        sourceUrl.focus();
        return;
      }
      const selected = [];
      for (const checkbox of root.querySelectorAll("[data-company-include]:checked:not(:disabled)")) {
        const key = checkbox.dataset.companyInclude;
        const value = values.get(key).value.trim();
        if (!value) continue;
        const addressKey = ["street", "house_number", "postal", "city", "state", "country"].includes(key);
        const target = addressKey
          ? document.querySelector(`[data-company-hidden-address="${key}"]`)
          : document.querySelector(`[data-company-edit-form] [name="${key}"]`);
        if (!target || (!addressKey && target.value.trim())) continue;
        target.value = value;
        if (key === "company") target.dispatchEvent(new Event("input", {bubbles: true}));
        selected.push(key);
      }
      if (!selected.length) {
        status.textContent = "Es wurde kein neues Feld markiert; bestehende Kontaktdaten bleiben unverändert.";
        return;
      }
      sourceHidden.value = source.href;
      fieldsHidden.value = selected.join(",");
      status.textContent = "Felder übernommen. Quelle und Zeitpunkt werden beim Speichern protokolliert; Änderungen bleiben bis zum Speichern lokal im Formular.";
    });

    updateLinks();
  });
})();
