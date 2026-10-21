(() => {
  "use strict";
  const body = document.querySelector("#invoice-lines tbody");
  if (!body) return;

  const updateControls = () => {
    const rows = Array.from(body.querySelectorAll(":scope > tr"));
    rows.forEach((row, index) => {
      row.querySelector(".move-line-up").disabled = index === 0;
      row.querySelector(".move-line-down").disabled = index === rows.length - 1;
    });
  };

  body.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    const row = button.closest("tr");
    if (!row) return;
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
