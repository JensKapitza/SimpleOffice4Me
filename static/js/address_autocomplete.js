(() => {
  "use strict";

  const debounce = (callback, delay = 300) => {
    let timer;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback(...args), delay);
    };
  };

  function initAddressAutocomplete(group) {
    const endpoint = group.dataset.addressEndpoint;
    const fields = Object.fromEntries(Array.from(group.querySelectorAll("[data-address-field]")).map((field) => [field.dataset.addressField, field]));
    if (!endpoint || !fields.city || !fields.postal || !fields.street) return;

    const searchable = [fields.city, fields.postal, fields.street];
    const status = group.querySelector("[data-address-status]");
    const results = group.querySelector("[data-address-results]");
    let requestNumber = 0;
    let controller;
    const setStatus = (message) => { if (status) status.textContent = message; };
    const query = () => searchable.map((field) => field.value.trim()).filter(Boolean).join(" ");
    const clearResults = () => { if (results) { results.replaceChildren(); results.hidden = true; } };
    const fill = (item) => {
      fields.city.value = item.city || fields.city.value;
      fields.postal.value = item.postal || fields.postal.value;
      fields.street.value = item.street || fields.street.value;
      if (fields.country) fields.country.value = (item.country || fields.country.value || "DE").toUpperCase();
      searchable.forEach((field) => field.dispatchEvent(new Event("change", { bubbles: true })));
    };
    const renderCandidates = (candidates) => {
      clearResults();
      if (!results || !candidates.length) return;
      candidates.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action small";
        button.setAttribute("role", "option");
        button.textContent = item.display_name || [item.city, item.postal, item.street, item.country].filter(Boolean).join(", ");
        button.addEventListener("click", () => { fill(item); clearResults(); setStatus("Adresse übernommen."); });
        results.append(button);
      });
      results.hidden = false;
    };

    const search = async () => {
      const q = query();
      if (q.length < 3) {
        requestNumber += 1;
        controller?.abort();
        clearResults();
        setStatus("");
        return;
      }
      const currentRequest = ++requestNumber;
      controller?.abort();
      controller = new AbortController();
      setStatus("Lokale Adressen werden gesucht …");
      try {
        const country = (fields.country?.value || "DE").trim().toLowerCase();
        const response = await fetch(`${endpoint}?q=${encodeURIComponent(q)}&country=${encodeURIComponent(country)}`, { headers: { Accept: "application/json" }, signal: controller.signal });
        const payload = await response.json();
        if (currentRequest !== requestNumber) return;
        if (!response.ok) throw new Error(payload.error || "lookup_failed");
        if (!payload.ready) { clearResults(); setStatus("Der lokale Adressindex ist noch nicht aufgebaut."); }
        else if (payload.unique) { fill(payload.unique); clearResults(); setStatus("Eindeutige Adresse automatisch vervollständigt."); }
        else {
          const candidates = payload.candidates || [];
          renderCandidates(candidates);
          setStatus(candidates.length ? `${candidates.length} Treffer – bitte auswählen.` : "Keine passende lokale Adresse gefunden.");
        }
      } catch (error) {
        if (error.name !== "AbortError") { clearResults(); setStatus("Lokale Adresssuche ist nicht verfügbar."); }
      }
    };

    const delayedSearch = debounce(search);
    searchable.forEach((field) => {
      field.setAttribute("autocomplete", "off");
      field.setAttribute("aria-autocomplete", "list");
      if (results?.id) field.setAttribute("aria-controls", results.id);
      field.addEventListener("input", delayedSearch);
      field.addEventListener("focus", () => { if (query().length >= 3) delayedSearch(); });
    });
    fields.country?.addEventListener("input", delayedSearch);
    group.querySelector("[data-address-search]")?.addEventListener("click", search);
    document.addEventListener("click", (event) => { if (!group.contains(event.target)) clearResults(); });
  }

  document.querySelectorAll("[data-address-autocomplete]").forEach(initAddressAutocomplete);
  document.querySelectorAll("form[data-address-compose]").forEach((form) => {
    form.addEventListener("submit", () => {
      const value = (name) => form.querySelector(`[data-address-field="${name}"]`)?.value.trim() || "";
      const target = form.querySelector("[data-address-composed]");
      if (target) target.value = `${value("street")}, ${value("postal")} ${value("city")}`.trim();
    });
  });
})();
