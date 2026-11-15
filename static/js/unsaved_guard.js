(() => {
  "use strict";

  const dirtyForms = new Set();
  let unloadHandlerAttached = false;

  const shouldTrack = (form) => form instanceof HTMLFormElement
    && String(form.method || "get").toLowerCase() === "post"
    && form.dataset.noUnsavedGuard !== "true"
    && !form.matches('[role="search"]');

  const beforeUnload = (event) => {
    if (dirtyForms.size === 0) return;
    event.preventDefault();
    event.returnValue = "";
  };

  const syncUnloadHandler = () => {
    const shouldWarn = dirtyForms.size > 0;
    if (shouldWarn && !unloadHandlerAttached) {
      window.addEventListener("beforeunload", beforeUnload);
      unloadHandlerAttached = true;
    } else if (!shouldWarn && unloadHandlerAttached) {
      window.removeEventListener("beforeunload", beforeUnload);
      unloadHandlerAttached = false;
    }
  };

  const markDirty = (form) => {
    if (!shouldTrack(form) || dirtyForms.has(form)) return;
    dirtyForms.add(form);
    form.dataset.soDirty = "1";
    syncUnloadHandler();
  };

  const markClean = (form) => {
    if (!(form instanceof HTMLFormElement) || !dirtyForms.delete(form)) return;
    delete form.dataset.soDirty;
    syncUnloadHandler();
  };

  const formFor = (target) => {
    if (!(target instanceof Element)) return null;
    const form = target.closest("form");
    return shouldTrack(form) ? form : null;
  };

  document.addEventListener("input", (event) => {
    const form = formFor(event.target);
    if (form) markDirty(form);
  }, {passive: true});

  document.addEventListener("change", (event) => {
    const form = formFor(event.target);
    if (form) markDirty(form);
  }, {passive: true});

  document.addEventListener("reset", (event) => {
    const form = event.target;
    if (form instanceof HTMLFormElement) window.requestAnimationFrame(() => markClean(form));
  });

  document.addEventListener("submit", (event) => markClean(event.target));
})();
