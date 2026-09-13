"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const selections = Array.from(document.querySelectorAll(".combine-contact-select"));
  const rows = Array.from(document.querySelectorAll(".combine-contact-row"));
  const count = document.getElementById("combine-selection-count");
  const submit = document.getElementById("combine-submit");
  const selectAll = document.getElementById("combine-select-all");
  const clear = document.getElementById("combine-clear");

  const update = () => {
    const selected = selections.filter((item) => item.checked).length;
    if (count) count.textContent = `${selected} ${count.dataset.label || ""}`;
    if (submit) submit.disabled = selected < 2;
    rows.forEach((row) => {
      const checkbox = row.querySelector(".combine-contact-select");
      row.classList.toggle("table-primary", Boolean(checkbox?.checked));
      row.setAttribute("aria-selected", checkbox?.checked ? "true" : "false");
    });
  };

  const setAll = (checked) => {
    selections.forEach((item) => { item.checked = checked; });
    update();
  };

  selections.forEach((item) => item.addEventListener("change", update));
  selectAll?.addEventListener("click", () => setAll(true));
  clear?.addEventListener("click", () => setAll(false));

  rows.forEach((row) => {
    const toggle = (event) => {
      if (event.target instanceof Element && event.target.closest("a,button,input,select,textarea,label")) return;
      const checkbox = row.querySelector(".combine-contact-select");
      if (!checkbox) return;
      checkbox.checked = !checkbox.checked;
      update();
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (event) => {
      if (event.key !== " " && event.key !== "Enter") return;
      event.preventDefault();
      toggle(event);
    });
  });

  submit?.addEventListener("click", (event) => {
    if (submit.disabled || !window.confirm(submit.dataset.confirm || "")) event.preventDefault();
  });

  update();
});
