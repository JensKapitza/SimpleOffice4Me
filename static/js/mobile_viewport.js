(() => {
  "use strict";

  const root = document.documentElement;
  const viewport = window.visualViewport;
  let frame = 0;

  const activeEditable = () => {
    const active = document.activeElement;
    return active instanceof HTMLElement
      && active.matches("input:not([type=hidden]), textarea, select, [contenteditable='true']");
  };

  const measure = () => {
    frame = 0;
    const layoutHeight = Math.max(document.documentElement.clientHeight, window.innerHeight || 0);
    const visualHeight = viewport ? viewport.height : layoutHeight;
    const offsetTop = viewport ? viewport.offsetTop : 0;
    const rawInset = Math.max(0, layoutHeight - visualHeight - offsetTop);
    const keyboardInset = activeEditable() && rawInset >= 80 ? Math.round(rawInset) : 0;
    root.style.setProperty("--so-visual-viewport-height", `${Math.round(visualHeight)}px`);
    root.style.setProperty("--so-keyboard-inset", `${keyboardInset}px`);
    if (keyboardInset > 0) root.dataset.soKeyboardOpen = "1";
    else delete root.dataset.soKeyboardOpen;
  };

  const schedule = () => {
    if (frame) return;
    frame = window.requestAnimationFrame(measure);
  };

  const keepFocusedControlVisible = () => {
    schedule();
    window.setTimeout(() => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !activeEditable()) return;
      const visibleTop = viewport ? viewport.offsetTop : 0;
      const visibleBottom = visibleTop + (viewport ? viewport.height : window.innerHeight);
      const rect = active.getBoundingClientRect();
      if (rect.bottom > visibleBottom - 16 || rect.top < visibleTop + 8) {
        active.scrollIntoView({block: "nearest", inline: "nearest", behavior: "auto"});
      }
      schedule();
    }, 120);
  };

  const installAndroidBackButton = () => {
    if (!window.SimpleOfficeAndroid || document.getElementById("android-history-back")) return;
    const navbar = document.querySelector(".app-navbar .container-fluid");
    if (!navbar) return;
    const button = document.createElement("button");
    button.id = "android-history-back";
    button.type = "button";
    button.className = "btn btn-sm btn-outline-light me-2";
    button.setAttribute("aria-label", "Zurück");
    button.title = "Zurück";
    button.innerHTML = '<i class="fa-solid fa-arrow-left" aria-hidden="true"></i><span class="visually-hidden">Zurück</span>';
    button.addEventListener("click", () => {
      if (window.history.length > 1) window.history.back();
      else window.location.assign("/");
    });
    const brand = navbar.querySelector(".navbar-brand");
    if (brand) brand.insertAdjacentElement("beforebegin", button);
    else navbar.prepend(button);
  };

  const showAndroidBuildInfo = async () => {
    if (!window.SimpleOfficeAndroid) return;
    installAndroidBackButton();
    try {
      const response = await fetch("/static/android-build.json", {
        cache: "no-store",
        credentials: "same-origin",
        headers: {"Accept": "application/json"},
      });
      if (!response.ok) return;
      const info = await response.json();
      const version = String(info?.version || "").trim();
      const architecture = String(info?.architecture || "").trim();
      const python = String(info?.python || "").trim();
      if (!version || !architecture) return;
      root.dataset.soAndroidApp = "1";
      root.dataset.soAndroidArchitecture = architecture;
      const existing = document.getElementById("android-build-info");
      if (existing) existing.remove();
      const labels = Array.from(document.querySelectorAll(".app-navbar .navbar-text"));
      const webVersion = labels.find((item) => String(item.textContent || "").trim().startsWith("Version "));
      if (!webVersion) return;
      const badge = document.createElement("span");
      badge.id = "android-build-info";
      badge.className = "navbar-text px-xxl-2 small text-nowrap";
      badge.textContent = `APK ${version} · ${architecture}`;
      badge.title = `Android APK · ABI ${String(info?.abi || architecture)} · Python ${python || "unbekannt"} · minSdk ${Number(info?.min_sdk || 24)}`;
      webVersion.insertAdjacentElement("afterend", badge);
    } catch (_error) {
    }
  };

  if (viewport) {
    viewport.addEventListener("resize", schedule, {passive: true});
    viewport.addEventListener("scroll", schedule, {passive: true});
  }
  window.addEventListener("resize", schedule, {passive: true});
  window.addEventListener("orientationchange", schedule, {passive: true});
  document.addEventListener("focusin", keepFocusedControlVisible, true);
  document.addEventListener("focusout", () => window.setTimeout(schedule, 80), true);
  document.addEventListener("visibilitychange", schedule);
  measure();
  showAndroidBuildInfo();
})();