"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const boxes = Array.from(document.querySelectorAll(".attachment-select"));
  const count = document.getElementById("attachment-selection-count");
  const submit = document.getElementById("attachment-review-submit");
  const selectAll = document.getElementById("attachment-select-all");
  const selectNone = document.getElementById("attachment-select-none");

  const update = () => {
    const selected = boxes.filter((box) => box.checked).length;
    if (count) count.textContent = `${selected} ausgewählt`;
    if (submit) submit.disabled = selected === 0;
    boxes.forEach((box) => box.closest(".attachment-option")?.classList.toggle("list-group-item-primary", box.checked));
  };

  const setAll = (checked) => {
    boxes.forEach((box) => { box.checked = checked; });
    update();
  };

  boxes.forEach((box) => box.addEventListener("change", update));
  selectAll?.addEventListener("click", () => setAll(true));
  selectNone?.addEventListener("click", () => setAll(false));
  update();
});
