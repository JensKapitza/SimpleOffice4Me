(() => {
  "use strict";
  const body = document.querySelector("#invoice-lines tbody");
  if (!body) return;

  const updateControls = () => {
    const rows = Array.from(body.querySelectorAll(":scope > tr"));
    rows.forEach((row, index) => {
      const up = row.querySelector(".move-line-up");
      const down = row.querySelector(".move-line-down");
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows.length - 1;
    });
  };

  body.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("button.move-line-up, button.move-line-down");
    if (!button || !body.contains(button)) return;
    const row = button.closest("tr");
    if (!row || row.parentElement !== body) return;
    event.preventDefault();
    if (button.classList.contains("move-line-up") && row.previousElementSibling) {
      body.insertBefore(row, row.previousElementSibling);
    } else if (button.classList.contains("move-line-down") && row.nextElementSibling) {
      body.insertBefore(row.nextElementSibling, row);
    }
    updateControls();
  });

  new MutationObserver(updateControls).observe(body, { childList: true });
  updateControls();
})();
