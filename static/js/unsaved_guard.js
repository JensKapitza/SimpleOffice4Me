(() => {
  "use strict";

  const trackedForms = new WeakSet();
  let dirtyForms = 0;

  const shouldTrack = (form) => form instanceof HTMLFormElement
    && String(form.method || "get").toLowerCase() === "post"
    && form.dataset.noUnsavedGuard !== "true"
    && !form.matches('[role="search"]');

  const markDirty = (form) => {
    if (!shouldTrack(form) || form.dataset.soDirty === "1") return;
    form.dataset.soDirty = "1";
    dirtyForms += 1;
  };

  const markClean = (form) => {
    if (!(form instanceof HTMLFormElement) || form.dataset.soDirty !== "1") return;
    delete form.dataset.soDirty;
    dirtyForms = Math.max(0, dirtyForms - 1);
  };

  const bind = (form) => {
    if (!shouldTrack(form) || trackedForms.has(form)) return;
    trackedForms.add(form);
    form.addEventListener("input", () => markDirty(form), {passive: true});
    form.addEventListener("change", () => markDirty(form), {passive: true});
    form.addEventListener("reset", () => window.requestAnimationFrame(() => markClean(form)));
    form.addEventListener("submit", () => markClean(form));
  };

  document.querySelectorAll("form").forEach(bind);
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (!(node instanceof Element)) return;
      if (node.matches("form")) bind(node);
      node.querySelectorAll?.("form").forEach(bind);
    }));
  }).observe(document.documentElement, {subtree: true, childList: true});

  window.addEventListener("beforeunload", (event) => {
    if (dirtyForms < 1) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
