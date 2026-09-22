(() => {
  "use strict";

  const form = document.getElementById("contact-qr-fields");
  if (!(form instanceof HTMLFormElement)) return;

  const endpoint = form.dataset.endpoint || "";
  const image = document.getElementById("contact-qr-preview-image");
  const status = document.getElementById("contact-qr-status");
  const download = document.getElementById("contact-qr-download");
  const submit = document.getElementById("contact-qr-generate");
  const allButton = document.getElementById("contact-qr-all");
  const standardButton = document.getElementById("contact-qr-standard");
  const minimalButton = document.getElementById("contact-qr-minimal");
  let objectUrl = "";
  let timer = 0;

  const boxes = () => Array.from(form.querySelectorAll('input[name="field"]'));

  const selectedFields = () => boxes()
    .filter((input) => input instanceof HTMLInputElement && input.checked)
    .map((input) => input.value);

  const requestUrl = (downloadFile = false) => {
    const url = new URL(endpoint, window.location.href);
    selectedFields().forEach((field) => url.searchParams.append("field", field));
    if (downloadFile) url.searchParams.set("download", "1");
    return url.toString();
  };

  const setStatus = (message, kind = "secondary") => {
    if (!status) return;
    status.className = `alert alert-${kind} py-2 mb-0`;
    status.textContent = message;
    status.hidden = false;
  };

  const refresh = async () => {
    if (!endpoint || !(image instanceof HTMLImageElement)) return;
    if (submit instanceof HTMLButtonElement) submit.disabled = true;
    setStatus("QR-Code wird lokal erzeugt …", "secondary");
    try {
      const response = await fetch(requestUrl(false), {
        headers: {"Accept": "image/svg+xml"},
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        const message = (await response.text()).trim();
        throw new Error(message || "QR-Code konnte nicht erzeugt werden.");
      }
      const blob = await response.blob();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      image.src = objectUrl;
      image.hidden = false;
      if (download instanceof HTMLAnchorElement) {
        download.href = requestUrl(true);
        download.hidden = false;
      }
      setStatus(
        `QR-Code bereit: ${selectedFields().length} Auswahl(en). Auf dem anderen Handy mit der Kamera bzw. Kontakt-App scannen.`,
        "success",
      );
    } catch (error) {
      image.hidden = true;
      if (download instanceof HTMLAnchorElement) download.hidden = true;
      setStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      if (submit instanceof HTMLButtonElement) submit.disabled = false;
    }
  };

  const scheduleRefresh = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 180);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    refresh();
  });
  form.addEventListener("change", scheduleRefresh);

  allButton?.addEventListener("click", () => {
    boxes().forEach((input) => {
      if (input instanceof HTMLInputElement && !input.disabled) input.checked = true;
    });
    scheduleRefresh();
  });

  standardButton?.addEventListener("click", () => {
    boxes().forEach((input) => {
      if (!(input instanceof HTMLInputElement) || input.disabled) return;
      input.checked = input.dataset.default === "1";
    });
    scheduleRefresh();
  });

  minimalButton?.addEventListener("click", () => {
    boxes().forEach((input) => {
      if (!(input instanceof HTMLInputElement) || input.disabled) return;
      input.checked = false;
    });
    scheduleRefresh();
  });

  window.addEventListener("beforeunload", () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  refresh();
})();