(() => {
  const palette = [
    ["text-bg-primary", "border-primary"],
    ["text-bg-success", "border-success"],
    ["text-bg-danger", "border-danger"],
    ["text-bg-warning", "border-warning"],
    ["text-bg-info", "border-info"],
    ["text-bg-secondary", "border-secondary"],
    ["text-bg-dark", "border-dark"],
  ];

  const actorStyle = (actor) => {
    let hash = 2166136261;
    for (const character of actor) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return palette[(hash >>> 0) % palette.length];
  };

  const initializeHistory = (history) => {
    const entries = [...history.querySelectorAll("[data-contact-history-entry]")];
    const actorFilter = history.querySelector("[data-contact-history-actor-filter]");
    const fieldFilter = history.querySelector("[data-contact-history-field-filter]");
    const count = history.querySelector("[data-contact-history-count]");
    const empty = history.querySelector("[data-contact-history-empty]");

    for (const entry of entries) {
      const [badgeClass, borderClass] = actorStyle(entry.dataset.contactHistoryActor || "");
      entry.classList.add(borderClass);
      entry.querySelector("[data-contact-history-actor-badge]")?.classList.add(badgeClass);
    }

    const applyFilters = () => {
      let visible = 0;
      for (const entry of entries) {
        const matchesActor = !actorFilter?.value || entry.dataset.contactHistoryActor === actorFilter.value;
        const matchesField = !fieldFilter?.value || entry.dataset.contactHistoryField === fieldFilter.value;
        entry.hidden = !(matchesActor && matchesField);
        if (!entry.hidden) visible += 1;
      }
      if (count) {
        count.textContent = document.documentElement.lang === "en"
          ? `${visible} of ${entries.length} changes`
          : `${visible} von ${entries.length} Änderungen`;
      }
      if (empty) empty.hidden = visible !== 0 || entries.length === 0;
    };

    actorFilter?.addEventListener("change", applyFilters);
    fieldFilter?.addEventListener("change", applyFilters);
    applyFilters();
  };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-contact-history]").forEach(initializeHistory);
  });
})();
