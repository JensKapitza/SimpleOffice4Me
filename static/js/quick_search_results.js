/* Progressive enhancement for the quick-search result list. No network requests. */
(() => {
  "use strict";
  const root = document.querySelector("[data-quick-search-results]");
  if (!root) return;
  const rows = Array.from(root.querySelectorAll("[data-quick-match]"));
  const toolbar = root.querySelector("[data-quick-filters]");
  const filters = Array.from(root.querySelectorAll("[data-quick-filter]"));
  const empty = root.querySelector("[data-quick-empty-filter]");
  const feedback = document.getElementById("quick-search-copy-feedback");
  if (toolbar && rows.length) toolbar.hidden = false;
  function choose(kind) {
    let visible = 0;
    rows.forEach((row) => {
      row.hidden = kind !== "all" && row.dataset.quickMatch !== kind;
      if (!row.hidden) visible += 1;
    });
    filters.forEach((button) => {
      const selected = button.dataset.quickFilter === kind;
      button.setAttribute("aria-pressed", String(selected));
      button.classList.toggle("btn-primary", selected);
      button.classList.toggle("btn-outline-secondary", !selected);
    });
    if (empty) empty.hidden = visible > 0;
  }
  filters.forEach((button) => button.addEventListener("click", () => choose(button.dataset.quickFilter)));
  const buttons = root.querySelectorAll("[data-copy-label]");
  buttons.forEach((button) => {
    // File labels are already present in the page; never request or expose file content.
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      button.hidden = false;
      button.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(button.dataset.copyLabel || "");
          if (feedback) feedback.textContent = document.documentElement.lang === "en" ? "Name copied." : "Bezeichnung kopiert.";
        } catch {
          if (feedback) feedback.textContent = document.documentElement.lang === "en" ? "Copy failed. Select the name manually." : "Kopieren fehlgeschlagen. Bezeichnung manuell markieren.";
        }
      });
    }
  });
})();
