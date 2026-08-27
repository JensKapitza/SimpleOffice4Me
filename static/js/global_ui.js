(() => {
  "use strict";

  const main = document.querySelector("main");
  if (main && !document.getElementById("main-content")) {
    main.id = "main-content";
    main.tabIndex = -1;
  }

  document.querySelectorAll('a[target="_blank"]').forEach((link) => {
    const rel = new Set((link.getAttribute("rel") || "").split(/\s+/).filter(Boolean));
    rel.add("noopener");
    link.setAttribute("rel", Array.from(rel).join(" "));
  });

  const searchInputs = () => Array.from(document.querySelectorAll(
    'input[type="search"], input[name="q"], input[name="search"]'
  )).filter((input) => !input.disabled && input.offsetParent !== null);

  document.querySelectorAll('input[name="q"], input[name="search"]').forEach((input) => {
    if (!input.hasAttribute("type") || input.type === "text") input.type = "search";
    if (input.form && !input.form.hasAttribute("role")) input.form.setAttribute("role", "search");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target instanceof HTMLElement && event.target.matches("input, textarea, select, [contenteditable]")) return;
    const search = searchInputs()[0];
    if (!search) return;
    event.preventDefault();
    search.focus();
    search.select();
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented || form.dataset.allowMultipleSubmit === "true") return;
    window.requestAnimationFrame(() => {
      if (event.defaultPrevented || !form.isConnected) return;
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll('button[type="submit"], input[type="submit"], button:not([type])').forEach((control) => {
        control.disabled = true;
        control.setAttribute("aria-disabled", "true");
      });
    });
  });

  window.addEventListener("pageshow", () => {
    document.querySelectorAll('form[data-submitting="true"]').forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll('[aria-disabled="true"]').forEach((control) => {
        control.disabled = false;
        control.removeAttribute("aria-disabled");
      });
    });
  });
})();
