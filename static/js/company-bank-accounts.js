(() => {
  "use strict";

  const newId = () => {
    const value = globalThis.crypto?.randomUUID?.();
    return value ? value.replaceAll("-", "") : Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
  };

  const init = () => {
    for (const root of document.querySelectorAll("[data-company-bank-accounts]")) {
      // Both the header script and the editor footer may load this module.
      if (root.dataset.bankAccountsInitialized === "true") continue;
      const rows = root.querySelector("[data-bank-account-rows]");
      const template = root.querySelector("[data-bank-account-template]");
      const add = root.querySelector("[data-add-bank-account]");
      if (!rows || !template || !add) continue;
      root.dataset.bankAccountsInitialized = "true";

      const wire = row => {
        const id = row.querySelector('[name="bank_account_id"]');
        const radio = row.querySelector('[name="default_bank_account_id"]');
        if (id && !id.value) id.value = newId();
        if (radio && id) radio.value = id.value;
        row.querySelector("[data-remove-bank-account]")?.addEventListener("click", () => {
          const wasDefault = Boolean(radio?.checked);
          row.remove();
          if (wasDefault) {
            // Radio is removed from the DOM; reselect a remaining account.
            const remaining = rows.querySelector('[name="default_bank_account_id"]');
            if (remaining) remaining.checked = true;
          }
        });
      };

      rows.querySelectorAll("[data-bank-account-row]").forEach(wire);
      add.addEventListener("click", () => {
        if (rows.querySelectorAll("[data-bank-account-row]").length >= 20) return;
        const row = template.content.firstElementChild.cloneNode(true);
        rows.append(row);
        wire(row);
        if (rows.querySelectorAll("[data-bank-account-row]").length === 1) {
          row.querySelector('[name="default_bank_account_id"]').checked = true;
        }
        row.querySelector('[name="bank_account_label"]')?.focus();
      });
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, {once: true});
  } else {
    init();
  }
})();
