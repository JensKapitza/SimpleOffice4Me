(() => {
  "use strict";

  const autocompleteByKey = {
    first_name: "given-name",
    firstname: "given-name",
    last_name: "family-name",
    lastname: "family-name",
    display_name: "name",
    name: "name",
    email: "email",
    phone: "tel",
    telephone: "tel",
    mobile: "tel",
    website: "url",
    url: "url",
    company: "organization",
    organization: "organization",
    street: "street-address",
    address: "street-address",
    city: "address-level2",
    state: "address-level1",
    postal: "postal-code",
    postcode: "postal-code",
    country: "country",
    birthday: "bday",
  };

  const keyFor = (control) => String(control.name || control.id || "")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_");

  const findAutocomplete = (control) => {
    const key = keyFor(control);
    if (autocompleteByKey[key]) return autocompleteByKey[key];
    return Object.entries(autocompleteByKey).find(([candidate]) => key.includes(candidate))?.[1] || "";
  };

  const autocompleteInputTypes = new Set([
    "color", "date", "datetime-local", "email", "hidden", "month",
    "number", "password", "range", "search", "tel", "text", "time",
    "url", "week",
  ]);

  const enhanceInputHints = (control) => {
    if (!(control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement)) return;
    const key = keyFor(control);
    const autocompleteAllowed = !(control instanceof HTMLInputElement)
      || autocompleteInputTypes.has(control.type);
    if (autocompleteAllowed && !control.hasAttribute("autocomplete")) {
      const value = findAutocomplete(control);
      if (value) control.autocomplete = value;
    }
    if (control instanceof HTMLInputElement && !control.hasAttribute("inputmode")) {
      if (control.type === "email") control.inputMode = "email";
      else if (control.type === "tel" || key.includes("phone") || key.includes("mobile")) control.inputMode = "tel";
      else if (control.type === "url") control.inputMode = "url";
      else if (control.type === "number") control.inputMode = control.step && control.step !== "1" ? "decimal" : "numeric";
    }
    if (!control.hasAttribute("autocapitalize")) {
      if (control.type === "email" || control.type === "url" || key.includes("username")) control.setAttribute("autocapitalize", "none");
      else if (["first_name", "last_name", "display_name", "city", "street", "company", "title"].some((part) => key.includes(part))) control.setAttribute("autocapitalize", "words");
    }
    if ((control.type === "email" || control.type === "url" || control.type === "tel") && !control.hasAttribute("spellcheck")) {
      control.spellcheck = false;
    }
  };

  let counterId = 0;
  const enhanceLengthCounter = (control) => {
    if (!(control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement)) return;
    if (control.maxLength <= 0 || control.type === "password" || control.dataset.soLengthCounter === "1") return;
    control.dataset.soLengthCounter = "1";
    counterId += 1;
    const hint = document.createElement("div");
    hint.className = "form-text text-end so-length-counter";
    hint.id = `so-length-counter-${counterId}`;
    hint.setAttribute("aria-live", "polite");
    const update = () => { hint.textContent = `${control.value.length}/${control.maxLength}`; };
    control.insertAdjacentElement("afterend", hint);
    const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    describedBy.add(hint.id);
    control.setAttribute("aria-describedby", Array.from(describedBy).join(" "));
    control.addEventListener("input", update);
    update();
  };

  const labelFor = (control) => {
    if (!(control instanceof HTMLElement)) return null;
    if (control.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
      if (explicit) return explicit;
    }
    return control.closest("label");
  };

  const enhanceRequired = (control) => {
    if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) return;
    if (!control.required || control.dataset.soRequiredMarker === "1") return;
    control.dataset.soRequiredMarker = "1";
    const label = labelFor(control);
    if (!label || label.querySelector(".so-required-marker")) return;
    const marker = document.createElement("span");
    marker.className = "so-required-marker text-danger ms-1";
    marker.setAttribute("aria-hidden", "true");
    marker.textContent = "*";
    label.append(marker);
    if (!label.title) label.title = "Pflichtfeld";
  };

  const enhanceIconAction = (element) => {
    if (!(element instanceof HTMLElement) || element.hasAttribute("aria-label")) return;
    const visibleText = String(element.textContent || "").trim();
    if (visibleText) return;
    const label = String(element.getAttribute("title") || element.dataset.label || "").trim();
    if (label) element.setAttribute("aria-label", label);
  };

  const enhanceTableSemantics = (table) => {
    if (!(table instanceof HTMLTableElement)) return;
    table.querySelectorAll("thead th:not([scope])").forEach((cell) => cell.setAttribute("scope", "col"));
    table.querySelectorAll("tbody tr > th:first-child:not([scope])").forEach((cell) => cell.setAttribute("scope", "row"));
  };

  const enhanceNavigation = (root) => {
    root.querySelectorAll?.(".nav-link.active:not([aria-current])").forEach((link) => link.setAttribute("aria-current", "page"));
    root.querySelectorAll?.("[disabled]:not([aria-disabled])").forEach((element) => element.setAttribute("aria-disabled", "true"));
  };

  const enhance = (root = document) => {
    root.querySelectorAll?.("input, textarea").forEach((control) => {
      enhanceInputHints(control);
      enhanceLengthCounter(control);
    });
    root.querySelectorAll?.("input[required], select[required], textarea[required]").forEach(enhanceRequired);
    root.querySelectorAll?.("button, a.btn").forEach(enhanceIconAction);
    root.querySelectorAll?.("table").forEach(enhanceTableSemantics);
    enhanceNavigation(root);
  };

  enhance(document);
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node instanceof Element) enhance(node);
    }));
  });
  observer.observe(document.documentElement, {subtree: true, childList: true});
})();
