/* Accessible client-side filter and sorting for the bounded Smart View table. */
(() => {
  "use strict";

  document.querySelectorAll("[data-smart-folder-sort-filter]").forEach((table) => {
    const body = table.tBodies[0];
    if (!body) return;

    const input = document.querySelector(
      `[data-smart-folder-table-filter="${table.id}"]`,
    );
    const count = table.parentElement?.parentElement?.querySelector(
      "[data-smart-folder-table-count]",
    );
    const rows = Array.from(body.rows);

    const updateFilter = () => {
      const query = (input?.value || "").trim().toLocaleLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const matches = !query || row.textContent.toLocaleLowerCase().includes(query);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      if (count) count.textContent = `${visible} / ${rows.length}`;
    };

    input?.addEventListener("input", updateFilter);
    table.querySelectorAll("thead [data-smart-folder-sort]").forEach((button, column) => {
      button.addEventListener("click", () => {
        const header = button.closest("th");
        const ascending = header?.getAttribute("aria-sort") !== "ascending";
        table.querySelectorAll("thead th").forEach((cell) => {
          cell.setAttribute("aria-sort", "none");
          const indicator = cell.querySelector("[aria-hidden='true']");
          if (indicator) indicator.textContent = "↕";
        });
        header?.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        const indicator = button.querySelector("[aria-hidden='true']");
        if (indicator) indicator.textContent = ascending ? "↑" : "↓";
        rows.sort((left, right) => {
          const a = left.cells[column]?.textContent.trim() || "";
          const b = right.cells[column]?.textContent.trim() || "";
          return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }) * (ascending ? 1 : -1);
        });
        rows.forEach((row) => body.appendChild(row));
        updateFilter();
      });
    });
    updateFilter();
  });
})();
