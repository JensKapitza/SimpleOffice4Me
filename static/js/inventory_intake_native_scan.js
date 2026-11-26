/* Optional, document-scoped Android transport. Ordinary web/manual/photo flow is unchanged. */
(() => {
  "use strict";
  const path = "/inventory/capture/intake";
  if (window.top !== window || location.pathname !== path || location.search) return;
  const form = document.querySelector("#intake-capture");
  const field = form?.querySelector("#intake-barcode");
  const button = form?.querySelector("[data-intake-native-scan]");
  const status = form?.querySelector("[data-intake-native-scan-status]");
  const help = form?.querySelector("[data-intake-native-scan-help]");
  if (!field || !button || !status || !help) return;
  const en = document.documentElement?.lang === "en";
  const text = (de, english) => en ? english : de;
  let channel = null;
  let pending = null;
  const sameForm = () => document.querySelector("#intake-capture") === form &&
    field.isConnected && field.form === form && form.elements.namedItem("barcode") === field;
  const allowedValue = (value) => {
    if (!/^(?:[0-9]{8}|[0-9]{12}|[0-9]{13})$/.test(value) || /^0+$/.test(value)) return false;
    let sum = Number(value[value.length - 1]);
    for (let i = value.length - 2, weight = 3; i >= 0; i--, weight = 4 - weight) {
      sum += Number(value[i]) * weight;
    }
    return sum % 10 === 0;
  };
  const invalidate = (message = "", notifyNative = true) => {
    if (!pending) return;
    const {id} = pending;
    pending = null;
    button.disabled = false;
    status.textContent = message;
    if (notifyNative && channel) {
      try { channel.postMessage("cancel:" + id); } catch { /* The page or port is already gone. */ }
    }
  };
  const edited = () => invalidate(text("Eingabe geändert. Bitte bei Bedarf erneut scannen.",
    "Input changed. Scan again if needed."));
  field.addEventListener("input", edited);
  field.addEventListener("change", edited);
  form.addEventListener("change", (event) => { if (event.target.name === "photo") invalidate(); }, true);
  form.addEventListener("submit", () => invalidate(), true);
  form.addEventListener("reset", () => invalidate(), true);
  window.addEventListener("hashchange", () => invalidate());
  window.addEventListener("popstate", () => invalidate());
  window.navigation?.addEventListener("navigate", () => invalidate());
  window.addEventListener("pagehide", () => {
    invalidate();
    channel?.close();
    channel = null;
    button.hidden = help.hidden = true;
  });

  window.addEventListener("message", (event) => {
    if (event.data !== "simpleoffice:intake-barcode/v1" || event.ports.length !== 1 ||
        (event.origin && event.origin !== "null" && event.origin !== location.origin) ||
        (event.source && event.source !== window) || !sameForm() ||
        location.pathname !== path || location.search) return;
    invalidate();
    channel?.close();
    const port = event.ports[0];
    channel = port;
    port.onmessage = ({data}) => {
      if (port !== channel || !pending || typeof data !== "string" || data.length > 64) return;
      const [id, result, value, ...extra] = data.split(":");
      if (id !== pending.id || extra.length) return;
      const current = sameForm() && location.href === pending.url && field.value === pending.before;
      invalidate("", false);
      if (!current) return;
      if (result === "ok" && allowedValue(value)) {
        field.value = value;
        status.textContent = text("Barcode erkannt. Zum Speichern den privaten Entwurf anlegen.",
          "Barcode added. Choose Create private draft to save it.");
      } else if (result === "cancelled") {
        status.textContent = text("Scan abgebrochen. Du kannst den Barcode eingeben oder ein Foto wählen.",
          "Scan cancelled. You can enter the barcode or choose a photo.");
      } else if (result === "busy") {
        status.textContent = text("Scanner noch beschäftigt. Versuch es gleich erneut oder gib den Barcode ein.",
          "The scanner is busy. Try again shortly or enter the barcode.");
      } else if (result === "unavailable" || result === "error") {
        status.textContent = text("Android-Scanner nicht verfügbar. Bitte Barcode eingeben oder ein Foto wählen.",
          "Android scanner unavailable. Enter the barcode or choose a photo.");
      } else {
        status.textContent = text("Kein unterstützter Barcode erkannt. Bitte Barcode eingeben oder ein Foto wählen.",
          "No supported barcode found. Enter the barcode or choose a photo.");
      }
    };
    button.hidden = help.hidden = false;
  });

  button.addEventListener("click", (event) => {
    if (!event.isTrusted || pending || !channel || !sameForm() ||
        location.pathname !== path || location.search) return;
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
      (byte) => byte.toString(16).padStart(2, "0")).join("");
    pending = {id, url: location.href, before: field.value};
    button.disabled = true;
    status.textContent = text("Android-Scanner wird geprüft …", "Checking the Android scanner …");
    try {
      channel.postMessage("scan:" + id);
    } catch {
      invalidate(text("Android-Scanner nicht verfügbar. Bitte Barcode eingeben oder ein Foto wählen.",
        "Android scanner unavailable. Enter the barcode or choose a photo."), false);
    }
  });
})();
