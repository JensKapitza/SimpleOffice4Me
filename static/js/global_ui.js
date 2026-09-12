(() => {
  "use strict";

  document.documentElement.classList.add("js");

  const liveRegion = () => {
    let region = document.getElementById("simpleoffice-live-region");
    if (!region) {
      region = document.createElement("div");
      region.id = "simpleoffice-live-region";
      region.className = "visually-hidden";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "true");
      document.body.append(region);
    }
    return region;
  };

  const announce = (message) => {
    const region = liveRegion();
    region.textContent = "";
    window.requestAnimationFrame(() => {
      region.textContent = String(message || "");
    });
  };

  const main = document.querySelector("main");
  if (main) {
    if (!document.getElementById("main-content")) main.id = "main-content";
    if (!main.hasAttribute("tabindex")) main.tabIndex = -1;
    if (!main.hasAttribute("role")) main.setAttribute("role", "main");
  }

  const isEditable = (element) => element instanceof HTMLElement
    && element.matches("input, textarea, select, [contenteditable=''], [contenteditable='true']");

  const enhanceLink = (link) => {
    if (!(link instanceof HTMLAnchorElement) || link.target !== "_blank") return;
    const rel = new Set((link.getAttribute("rel") || "").split(/\s+/).filter(Boolean));
    rel.add("noopener");
    rel.add("noreferrer");
    link.setAttribute("rel", Array.from(rel).join(" "));
    try {
      if (new URL(link.href, window.location.href).origin !== window.location.origin) {
        link.referrerPolicy = "no-referrer";
      }
    } catch (_error) {
      // Invalid or incomplete links remain untouched beyond rel hardening.
    }
  };

  const searchSelector = 'input[type="search"], input[name="q"], input[name="search"], input[name="query"], input[data-search]';
  const normalizeSearch = (input) => {
    if (!(input instanceof HTMLInputElement)) return;
    if ((!input.hasAttribute("type") || input.type === "text")
        && ["q", "search", "query"].includes(input.name)) {
      input.type = "search";
    }
    if (input.type !== "search") return;
    if (input.form && !input.form.hasAttribute("role")) input.form.setAttribute("role", "search");
    if (!input.hasAttribute("enterkeyhint")) input.setAttribute("enterkeyhint", "search");
    if (!input.hasAttribute("autocapitalize")) input.setAttribute("autocapitalize", "none");
    if (!input.hasAttribute("spellcheck")) input.spellcheck = false;
  };

  const searchInputs = () => Array.from(document.querySelectorAll(searchSelector))
    .filter((input) => !input.disabled && input.offsetParent !== null);

  const humanizeFieldName = (name) => String(name || "")
    .replace(/[_\-.]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();

  const helpOverrides = {
    email: "E-Mail-Adresse, die für diesen Eintrag verwendet wird.",
    phone: "Telefonnummer für diesen Kontakt oder Vorgang.",
    telephone: "Telefonnummer für diesen Kontakt oder Vorgang.",
    password: "Passwort für diesen Zugang. Nicht in Notizen oder anderen Freitextfeldern speichern.",
    username: "Benutzername, mit dem sich die Person oder das Gerät anmeldet.",
    name: "Bezeichnung, unter der dieser Eintrag angezeigt wird.",
    title: "Kurzer, eindeutiger Titel des Eintrags.",
    description: "Zusätzliche Beschreibung. Nur Informationen eintragen, die für diesen Vorgang relevant sind.",
    date: "Datum für diesen Vorgang.",
    start: "Startzeit oder Startdatum des Vorgangs.",
    end: "Endzeit oder Enddatum des Vorgangs.",
    due: "Fälligkeit. Ab diesem Zeitpunkt sollte der Vorgang erledigt sein.",
    status: "Aktueller Bearbeitungsstand dieses Eintrags.",
    priority: "Priorität dieses Eintrags. Höhere Priorität bedeutet wichtigere oder dringendere Bearbeitung.",
    search: "Suchbegriff eingeben. Die Treffer werden anhand der verfügbaren Daten gefiltert.",
    q: "Suchbegriff eingeben. Die Treffer werden anhand der verfügbaren Daten gefiltert.",
  };

  const fieldLabel = (control) => {
    if (!(control instanceof HTMLElement)) return null;
    if (control.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
      if (explicit instanceof HTMLLabelElement) return explicit;
    }
    const wrapped = control.closest("label");
    if (wrapped instanceof HTMLLabelElement) return wrapped;
    const container = control.closest(".mb-3, .form-group, .col, [class*='col-'], .input-group");
    return container?.querySelector(":scope > label.form-label, :scope > label") || null;
  };

  const fieldHelpText = (control, label) => {
    const explicit = String(control.dataset.help || "").trim();
    if (explicit) return explicit;
    const key = String(control.name || control.id || "").toLowerCase();
    if (helpOverrides[key]) return helpOverrides[key];
    const matchingKey = Object.keys(helpOverrides).find((candidate) => key.includes(candidate));
    if (matchingKey) return helpOverrides[matchingKey];
    const labelText = String(label?.textContent || control.getAttribute("aria-label") || "").replace(/\s*\?\s*$/, "").trim();
    const friendly = labelText || humanizeFieldName(control.name || control.id) || "Dieses Feld";
    if (control instanceof HTMLSelectElement) return `${friendly}: gewünschte Option auswählen.`;
    if (control instanceof HTMLTextAreaElement) return `${friendly}: passende Informationen als Text eintragen.`;
    if (control instanceof HTMLInputElement) {
      if (control.type === "checkbox") return `${friendly}: aktivieren, wenn diese Option gelten soll.`;
      if (control.type === "radio") return `${friendly}: diese Auswahl verwenden.`;
      if (control.type === "file") return `${friendly}: passende Datei vom Gerät auswählen.`;
      if (control.type === "number") return `${friendly}: gültigen Zahlenwert eintragen.`;
      if (control.type === "date" || control.type === "datetime-local" || control.type === "time") return `${friendly}: passenden Zeitpunkt auswählen.`;
    }
    return `${friendly}: passenden Wert eintragen.`;
  };

  let fieldHelpCounter = 0;
  const enhanceFieldHelp = (control) => {
    if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) return;
    if (["hidden", "submit", "button", "reset", "image"].includes(control.type) || control.dataset.noHelp === "true") return;
    if (control.dataset.soHelpReady === "1") return;
    control.dataset.soHelpReady = "1";
    const label = fieldLabel(control);
    const helpText = fieldHelpText(control, label);
    if (!helpText) return;
    fieldHelpCounter += 1;
    const helpId = `so-field-help-${fieldHelpCounter}`;
    const help = document.createElement("span");
    help.className = "so-field-help d-inline-flex align-items-center justify-content-center ms-1";
    help.id = helpId;
    help.tabIndex = 0;
    help.setAttribute("role", "button");
    help.setAttribute("aria-label", `Hilfe: ${helpText}`);
    help.setAttribute("data-bs-toggle", "tooltip");
    help.setAttribute("data-bs-placement", "top");
    help.setAttribute("title", helpText);
    help.textContent = "?";
    if (label) label.append(help);
    else {
      const anchor = control.closest(".input-group") || control;
      anchor.insertAdjacentElement("afterend", help);
      if (!control.hasAttribute("aria-label")) {
        control.setAttribute("aria-label", humanizeFieldName(control.name || control.id) || "Eingabefeld");
      }
    }
    const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    describedBy.add(helpId);
    control.setAttribute("aria-describedby", Array.from(describedBy).join(" "));
    if (window.bootstrap?.Tooltip) window.bootstrap.Tooltip.getOrCreateInstance(help);
  };

  let feedbackCounter = 0;
  const connectInvalidFeedback = (control) => {
    if (!(control instanceof HTMLElement)) return;
    const feedback = control.parentElement?.querySelector(".invalid-feedback")
      || control.closest(".mb-3, .form-group, .input-group")?.querySelector(".invalid-feedback");
    if (!(feedback instanceof HTMLElement)) return;
    if (!feedback.id) {
      feedbackCounter += 1;
      feedback.id = `so-invalid-feedback-${feedbackCounter}`;
    }
    const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    describedBy.add(feedback.id);
    control.setAttribute("aria-describedby", Array.from(describedBy).join(" "));
  };

  const enhanceFileInput = (input) => {
    if (!(input instanceof HTMLInputElement) || input.type !== "file" || input.dataset.soFileStatus === "1") return;
    input.dataset.soFileStatus = "1";
    const status = document.createElement("div");
    status.className = "form-text so-file-selection";
    status.setAttribute("aria-live", "polite");
    input.insertAdjacentElement("afterend", status);
    const update = () => {
      const count = input.files?.length || 0;
      status.textContent = count === 0 ? "" : (count === 1 ? input.files[0].name : `${count} Dateien ausgewählt`);
    };
    input.addEventListener("change", update);
  };

  const enhanceMedia = (root) => {
    root.querySelectorAll?.("img").forEach((image) => {
      if (!image.hasAttribute("loading") && image.dataset.eager !== "true") image.loading = "lazy";
      if (!image.hasAttribute("decoding")) image.decoding = "async";
    });
    root.querySelectorAll?.("iframe").forEach((frame) => {
      if (!frame.hasAttribute("loading")) frame.loading = "lazy";
      if (!frame.hasAttribute("referrerpolicy")) frame.referrerPolicy = "same-origin";
    });
    root.querySelectorAll?.("video").forEach((video) => {
      if (!video.hasAttribute("playsinline")) video.setAttribute("playsinline", "");
      if (!video.hasAttribute("preload")) video.preload = "metadata";
    });
  };

  const enhanceTable = (table) => {
    if (!(table instanceof HTMLTableElement) || table.dataset.noResponsive === "true" || !table.parentElement) return;
    const parent = table.parentElement;
    if (Array.from(parent.classList).some((name) => name.startsWith("table-responsive"))) return;
    const wrapper = document.createElement("div");
    wrapper.className = "table-responsive";
    parent.insertBefore(wrapper, table);
    wrapper.append(table);
  };

  const enhanceAlert = (alert) => {
    if (!(alert instanceof HTMLElement) || alert.hasAttribute("role")) return;
    const urgent = alert.classList.contains("alert-danger");
    alert.setAttribute("role", urgent ? "alert" : "status");
    alert.setAttribute("aria-live", urgent ? "assertive" : "polite");
  };

  const enhance = (root = document) => {
    root.querySelectorAll?.('a[target="_blank"]').forEach(enhanceLink);
    root.querySelectorAll?.(searchSelector).forEach(normalizeSearch);
    root.querySelectorAll?.("input, select, textarea").forEach(enhanceFieldHelp);
    root.querySelectorAll?.("[required]").forEach((control) => {
      if (!control.hasAttribute("aria-required")) control.setAttribute("aria-required", "true");
      connectInvalidFeedback(control);
    });
    root.querySelectorAll?.('input[type="file"]').forEach(enhanceFileInput);
    root.querySelectorAll?.(".alert").forEach(enhanceAlert);
    root.querySelectorAll?.("table.table").forEach(enhanceTable);
    enhanceMedia(root);
  };

  enhance(document);

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node instanceof Element) {
        enhanceLink(node);
        normalizeSearch(node);
        enhance(node);
      }
    }));
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});

  document.addEventListener("keydown", (event) => {
    const key = String(event.key || "").toLowerCase();
    const shortcut = key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
      || key === "k" && (event.ctrlKey || event.metaKey) && !event.altKey;
    if (shortcut && !isEditable(event.target)) {
      const search = searchInputs()[0];
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
      return;
    }
    if (event.key === "Escape" && document.activeElement instanceof HTMLInputElement
        && document.activeElement.matches(searchSelector)) {
      document.activeElement.blur();
    }
  });

  document.addEventListener("invalid", (event) => {
    const control = event.target;
    if (!(control instanceof HTMLElement)) return;
    control.setAttribute("aria-invalid", "true");
    connectInvalidFeedback(control);
    const form = control.closest("form");
    if (!form || form.dataset.soInvalidFocusQueued === "1") return;
    form.dataset.soInvalidFocusQueued = "1";
    window.requestAnimationFrame(() => {
      delete form.dataset.soInvalidFocusQueued;
      const invalid = form.querySelector(":invalid");
      if (invalid instanceof HTMLElement) invalid.focus({preventScroll: false});
    });
  }, true);

  const clearInvalid = (event) => {
    const control = event.target;
    if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) return;
    if (control.checkValidity()) control.removeAttribute("aria-invalid");
  };
  document.addEventListener("input", clearInvalid, true);
  document.addEventListener("change", clearInvalid, true);

  const submitControls = (form) => Array.from(form.querySelectorAll(
    'button[type="submit"], input[type="submit"], button:not([type])'
  ));

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented || form.dataset.allowMultipleSubmit === "true") return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    window.requestAnimationFrame(() => {
      if (event.defaultPrevented || !form.isConnected) return;
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      submitControls(form).forEach((control) => {
        control.dataset.soWasDisabled = control.disabled ? "1" : "0";
        control.disabled = true;
        control.setAttribute("aria-disabled", "true");
      });
    });
  });

  const restoreSubmittingForms = () => {
    document.querySelectorAll('form[data-submitting="true"]').forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      submitControls(form).forEach((control) => {
        const wasDisabled = control.dataset.soWasDisabled === "1";
        control.disabled = wasDisabled;
        delete control.dataset.soWasDisabled;
        if (!wasDisabled) control.removeAttribute("aria-disabled");
      });
    });
  };
  window.addEventListener("pageshow", restoreSubmittingForms);

  document.addEventListener("click", (event) => {
    const navLink = event.target instanceof Element ? event.target.closest(".app-navbar .navbar-collapse.show .nav-link[href]") : null;
    if (navLink && window.bootstrap?.Collapse) {
      const collapse = navLink.closest(".navbar-collapse");
      if (collapse) window.bootstrap.Collapse.getOrCreateInstance(collapse, {toggle: false}).hide();
    }
  });

  const focusHashTarget = () => {
    if (!window.location.hash) return;
    let id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); } catch (_error) { return; }
    const target = document.getElementById(id);
    if (!(target instanceof HTMLElement)) return;
    if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({preventScroll: true});
  };
  window.addEventListener("hashchange", focusHashTarget);

  const fallbackCopy = (text) => {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    return copied;
  };

  document.addEventListener("click", async (event) => {
    const trigger = event.target instanceof Element ? event.target.closest("[data-copy-text], [data-copy-target]") : null;
    if (!(trigger instanceof HTMLElement)) return;
    let text = trigger.dataset.copyText || "";
    if (!text && trigger.dataset.copyTarget) {
      const target = document.querySelector(trigger.dataset.copyTarget);
      text = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ? target.value : target?.textContent || "";
    }
    if (!text) return;
    event.preventDefault();
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else if (!fallbackCopy(text)) throw new Error("copy failed");
      announce(trigger.dataset.copySuccess || "In Zwischenablage kopiert");
    } catch (_error) {
      announce("Kopieren nicht möglich");
    }
  });

  const updateNetworkState = () => {
    const online = navigator.onLine;
    document.body.dataset.networkStatus = online ? "online" : "offline";
    const banner = document.getElementById("network-offline-banner");
    if (banner) banner.hidden = online;
    announce(online ? "Verbindung wiederhergestellt" : "Offline. Netzwerkfunktionen sind vorübergehend nicht verfügbar.");
  };
  window.addEventListener("online", updateNetworkState);
  window.addEventListener("offline", updateNetworkState);
  document.body.dataset.networkStatus = navigator.onLine ? "online" : "offline";
  const initialOfflineBanner = document.getElementById("network-offline-banner");
  if (initialOfflineBanner) initialOfflineBanner.hidden = navigator.onLine;
})();
