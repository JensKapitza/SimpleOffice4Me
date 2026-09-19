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

  function initProjectDocumentSearch() {
    const query = document.querySelector("[data-project-document-query]");
    const results = document.querySelector("[data-project-document-results]");
    const documentId = document.querySelector("[data-project-document-id]");
    const attach = document.querySelector("[data-project-document-attach]");
    if (!query || !results || !documentId || !attach) return;

    let timer = 0;
    const message = (text) => {
      results.replaceChildren();
      const entry = document.createElement("div");
      entry.className = "list-group-item text-secondary";
      entry.textContent = text;
      results.append(entry);
    };
    const showResults = (items) => {
      results.replaceChildren();
      if (!items.length) {
        message("Keine Treffer.");
        return;
      }
      items.forEach((item) => {
        const button = document.createElement("button");
        const meta = document.createElement("small");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.append(document.createTextNode(item.path));
        meta.className = "d-block text-secondary";
        meta.textContent = item.state + " · " + item.document_id;
        button.append(meta);
        button.addEventListener("click", () => {
          documentId.value = item.document_id;
          query.value = item.path;
          attach.disabled = false;
          results.replaceChildren();
        });
        results.append(button);
      });
    };

    query.addEventListener("input", () => {
      documentId.value = "";
      attach.disabled = true;
      window.clearTimeout(timer);
      const value = query.value.trim();
      if (value.length < 2) {
        results.replaceChildren();
        return;
      }
      timer = window.setTimeout(async () => {
        try {
          const response = await fetch(query.dataset.searchUrl + "?q=" + encodeURIComponent(value), {
            headers: {Accept: "application/json"},
          });
          if (!response.ok) throw new Error("search");
          showResults(await response.json());
        } catch (_error) {
          message("Suche fehlgeschlagen.");
        }
      }, 250);
    });
  }

  function initDateOffsets() {
    document.querySelectorAll("[data-date-offset]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = button.closest("form")?.querySelector('[name="date"]');
        if (!input) return;
        const day = new Date();
        day.setDate(day.getDate() + Number(button.dataset.dateOffset || 0));
        input.value = day.toISOString().slice(0, 10);
      });
    });
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

  function openHashDetails() {
    const raw = window.location.hash.slice(1);
    if (!raw) return;
    let id = raw;
    try {
      id = decodeURIComponent(raw);
    } catch (_error) {
      return;
    }
    const target = document.getElementById(id);
    if (target?.tagName === "DETAILS") target.open = true;
  }

  function initProjectUi() {
    initProjectOverview();
    initTaskFilters();
    initProjectDocumentSearch();
    initDateOffsets();
    initSummaryCopy();
    openHashDetails();
    window.addEventListener("hashchange", openHashDetails);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initProjectUi);
  } else {
    initProjectUi();
  }
})();
