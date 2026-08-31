"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const selections = Array.from(document.querySelectorAll(".duplicate-select"));
  const selectAll = document.getElementById("select-all-safe");
  const count = document.getElementById("bulk-selection-count");
  const submit = document.getElementById("bulk-merge-button");
  const update = () => {
    const selected = selections.filter((item) => item.checked).length;
    if (count) count.textContent = `${selected} ${count.dataset.label || ""}`;
    if (submit) submit.disabled = selected === 0;
    if (selectAll) {
      selectAll.checked = selections.length > 0 && selected === selections.length;
      selectAll.indeterminate = selected > 0 && selected < selections.length;
    }
  };
  selectAll?.addEventListener("change", () => {
    selections.forEach((item) => { item.checked = selectAll.checked; });
    update();
  });
  selections.forEach((item) => item.addEventListener("change", update));
  document.querySelectorAll("[data-confirm]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!window.confirm(button.dataset.confirm || "")) event.preventDefault();
    });
  });
  update();
});
