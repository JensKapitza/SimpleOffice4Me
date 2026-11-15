(() => {
  "use strict";

  const enhanced = new WeakSet();
  const isEnglish = document.documentElement.lang.toLowerCase().startsWith("en");

  const labelFor = (visible) => {
    if (isEnglish) return visible ? "Hide password" : "Show password";
    return visible ? "Passwort verbergen" : "Passwort anzeigen";
  };

  const enhance = (input) => {
    if (!(input instanceof HTMLInputElement)
      || input.type !== "password"
      || input.dataset.noPasswordToggle === "true"
      || input.closest("[data-no-password-toggle]")
      || enhanced.has(input)) return;

    enhanced.add(input);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-outline-secondary";
    button.setAttribute("aria-pressed", "false");
    const icon = document.createElement("i");
    icon.className = "fa-solid fa-eye";
    icon.setAttribute("aria-hidden", "true");
    button.append(icon);

    const update = (visible) => {
      input.type = visible ? "text" : "password";
      button.setAttribute("aria-label", labelFor(visible));
      button.title = labelFor(visible);
      button.setAttribute("aria-pressed", String(visible));
      icon.className = visible ? "fa-solid fa-eye-slash" : "fa-solid fa-eye";
    };

    update(false);
    button.addEventListener("click", () => update(input.type === "password"));

    const parent = input.parentElement;
    if (parent?.classList.contains("input-group")) {
      input.insertAdjacentElement("afterend", button);
    } else if (parent) {
      const group = document.createElement("div");
      group.className = "input-group";
      parent.insertBefore(group, input);
      group.append(input, button);
    }
  };

  const scan = (root) => {
    if (root instanceof HTMLInputElement) enhance(root);
    root.querySelectorAll?.('input[type="password"]').forEach(enhance);
  };

  scan(document);
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node instanceof Element) scan(node);
    }));
  }).observe(document.documentElement, {subtree: true, childList: true});
})();
