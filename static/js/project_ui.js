(() => {
  "use strict";

  function normalize(value) {
    return String(value || "").trim().toLocaleLowerCase();
  }

  function initProjectOverview() {
    const root = document.querySelector('[data-project-overview]');
    if (!root) return;

    const search = root.querySelector('[data-project-search]');
    const status = root.querySelector('[data-project-status-filter]');
    const cards = Array.from(root.querySelectorAll('[data-project-card]'));
    const count = root.querySelector('[data-project-count]');
    const empty = root.querySelector('[data-project-filter-empty]');

    const apply = () => {
      const query = normalize(search?.value);
      const selectedStatus = status?.value || "";
      let visible = 0;

      cards.forEach((card) => {
        const matchesText = !query || normalize(card.textContent).includes(query);
        const matchesStatus = !selectedStatus || card.dataset.projectStatus === selectedStatus;
        const show = matchesText && matchesStatus;
        card.hidden = !show;
        if (show) visible += 1;
      });

      if (count) count.textContent = String(visible);
      if (empty) empty.hidden = visible !== 0 || cards.length === 0;
    };

    search?.addEventListener("input", apply);
    status?.addEventListener("change", apply);
    apply();
  }

  function initTaskFilters() {
    const root = document.querySelector('[data-project-tasks]');
    if (!root) return;

    const search = root.querySelector('[data-task-search]');
    const chips = Array.from(root.querySelectorAll('[data-task-status-filter]'));
    const cards = Array.from(root.querySelectorAll('[data-task-card]'));
    const count = root.querySelector('[data-task-count]');
    const empty = root.querySelector('[data-task-filter-empty]');
    let selectedStatus = "";

    const apply = () => {
      const query = normalize(search?.value);
      let visible = 0;

      cards.forEach((card) => {
        const matchesText = !query || normalize(card.textContent).includes(query);
        const matchesStatus = !selectedStatus || card.dataset.taskStatus === selectedStatus;
        const show = matchesText && matchesStatus;
        card.hidden = !show;
        if (show) visible += 1;
      });

      if (count) count.textContent = String(visible);
      if (empty) empty.hidden = visible !== 0 || cards.length === 0;
    };

    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        selectedStatus = chip.dataset.taskStatusFilter || "";
        chips.forEach((item) => {
          const active = item === chip;
          item.classList.toggle("active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        apply();
      });
    });

    search?.addEventListener("input", apply);
    apply();
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  function initSummaryCopy() {
    const buttons = Array.from(document.querySelectorAll('[data-project-summary-copy]'));
    buttons.forEach((button) => {
      button.addEventListener("click", async () => {
        const root = button.closest('[data-project-ui="v2"]') || document;
        const title = root.querySelector("[data-project-title]")?.textContent.trim() || "Projekt";
        const status = root.querySelector("[data-project-status]")?.textContent.trim() || "";
        const progress = root.querySelector("[data-project-progress]")?.textContent.trim() || "";
        const metrics = Array.from(root.querySelectorAll("[data-project-metric]"))
          .map((item) => item.textContent.replace(/\s+/g, " ").trim());
        const tasks = Array.from(root.querySelectorAll("[data-project-task-summary]"))
          .slice(0, 12)
          .map((item) => "- " + item.textContent.replace(/\s+/g, " ").trim());
        const text = [
          title,
          status ? "Status: " + status : "",
          progress ? "Fortschritt: " + progress : "",
          ...metrics,
          tasks.length ? "" : "",
          ...tasks,
        ].filter((line, index, lines) => line || (index > 0 && lines[index - 1])).join("\n");

        const original = button.innerHTML;
        try {
          await copyText(text);
          button.textContent = "Kopiert";
          window.setTimeout(() => {
            button.innerHTML = original;
          }, 1400);
        } catch (_error) {
          button.textContent = "Kopieren fehlgeschlagen";
          window.setTimeout(() => {
            button.innerHTML = original;
          }, 1800);
        }
      });
    });
  }

  function initProjectUi() {
    initProjectOverview();
    initTaskFilters();
    initSummaryCopy();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initProjectUi);
  } else {
    initProjectUi();
  }
})();
