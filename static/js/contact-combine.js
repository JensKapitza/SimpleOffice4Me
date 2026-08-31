"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const selections = Array.from(document.querySelectorAll(".combine-contact-select"));
  const count = document.getElementById("combine-selection-count");
  const submit = document.getElementById("combine-submit");
  const update = () => {
    const selected = selections.filter((item) => item.checked).length;
    if (count) count.textContent = `${selected} ${count.dataset.label || ""}`;
    if (submit) submit.disabled = selected < 2;
  };
  selections.forEach((item) => item.addEventListener("change", update));
  submit?.addEventListener("click", (event) => {
    if (!window.confirm(submit.dataset.confirm || "")) event.preventDefault();
  });
  update();
});
