(() => {
  "use strict";
  const form = document.getElementById("sms-device-form");
  if (!form || window !== window.top) return;
  const section = document.getElementById("sms-device-import");
  const previewButton = document.getElementById("sms-device-preview");
  const commitButton = document.getElementById("sms-device-commit");
  const from = document.getElementById("sms-device-from");
  const through = document.getElementById("sms-device-through");
  const status = document.getElementById("sms-device-status");
  const key = "simpleoffice:last-device-sms-import";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const text = (de, en) => form.dataset.language === "en" ? en : de;
  const messages = {
    preview_expired: text("Die Vorschau ist abgelaufen. Lade eine neue Vorschau.", "The preview has expired. Load a new preview."),
    preview_changed: text("SMS oder Archiv haben sich seit der Vorschau geändert. Nichts wurde hinzugefügt. Lade eine neue Vorschau.", "Messages or archive changed since the preview. Nothing was added. Load a new preview."),
    invalid_range: text("Prüfe den Zeitraum.", "Check the date range."),
    denied: text("Android hat den SMS-Zugriff nicht erteilt. Prüfe die App-Berechtigungen. Wenn die Freigabe fehlt, muss der Installationskanal SMS-Lesezugriff zulassen.", "Android did not grant SMS access. Check the app permissions. If access is unavailable, the installation channel must allow SMS read access."),
    unavailable: text("Der SMS-Import war nicht verfügbar. Prüfe das Archiv vor einem neuen Versuch.", "SMS import was unavailable. Check your archive before retrying."),
    too_many: text("Dieser Zeitraum ist zu groß. Wähle einen kleineren Zeitraum und versuche es erneut.", "This date range is too large. Select a shorter range and retry."),
    invalid_rows: text("Android lieferte eine ungültige SMS. Nichts wurde hinzugefügt. Wähle einen anderen Zeitraum.", "Android returned an invalid SMS. Nothing was added. Select a different date range."),
    cancelled: text("SMS-Import abgebrochen. Bereits gespeicherte SMS bleiben im Archiv.", "SMS import cancelled. Messages already saved remain in the archive."),
    conflict: text("Eine SMS widerspricht einem vorhandenen Archiveintrag. Nichts wurde überschrieben oder hinzugefügt.", "An SMS conflicts with an archived entry. Nothing was overwritten or added."),
    busy: text("Ein anderer SMS-Import läuft bereits.", "Another SMS import is already running."),
    unauthorized: text("Melde dich erneut in der App an.", "Sign in to the app again."),
  };
  let channel = null;
  let pending = "";
  let preview = "";
  let timer;
  let previous = "";

  function counts(data) {
    if (!["ok", "preview"].includes(data?.state) || !Number.isInteger(data.added) || !Number.isInteger(data.already) ||
        data.added < 0 || data.already < 0 || data.added + data.already > 1000) return "";
    if (data.state === "preview") {
      return text(`Vorschau: ${data.added} neue SMS, ${data.already} bereits archiviert. Noch nichts gespeichert.`,
        `Preview: ${data.added} new messages, ${data.already} already archived. Nothing saved yet.`);
    }
    return text(`${data.added} SMS hinzugefügt, ${data.already} bereits archiviert.`,
      `${data.added} messages added, ${data.already} already archived.`);
  }

  try {
    const cached = JSON.parse(sessionStorage.getItem(key));
    if (csrf && cached?.session === csrf) previous = counts(cached);
    sessionStorage.removeItem(key);
  } catch {
    // Reload is optional: import itself is already persisted locally.
  }

  function dropPreview() {
    preview = "";
    commitButton.hidden = true;
  }

  function setPending(stage) {
    pending = stage;
    const disabled = Boolean(stage);
    previewButton.disabled = commitButton.disabled = from.disabled = through.disabled = disabled;
  }

  function finish(data) {
    clearTimeout(timer);
    const stage = pending;
    setPending("");
    dropPreview();
    const count = counts(data);
    if (stage === "preview" && data?.state === "preview" && count && /^[a-f0-9]{32}$/.test(data.preview)) {
      preview = data.preview;
      commitButton.hidden = data.added === 0;
      status.textContent = count;
      return;
    }
    if (stage === "commit" && data?.state === "ok" && count) {
      status.textContent = count;
      try {
        sessionStorage.setItem(key, JSON.stringify({state: "ok", added: data.added, already: data.already, session: csrf}));
        location.replace("/communication/sms");
      } catch {
        // Storage restrictions do not undo the import; user can refresh manually.
      }
      return;
    }
    status.textContent = Object.prototype.hasOwnProperty.call(messages, data?.state)
      ? messages[data.state] : messages.unavailable;
  }

  window.addEventListener("message", (event) => {
    if (channel || event.data !== "simpleoffice:sms-device-import" || event.ports.length !== 1 ||
        (event.origin && event.origin !== "null" && event.origin !== location.origin) ||
        (event.source && event.source !== window) || location.pathname !== "/communication/sms" || location.search ||
        !["", "#sms-manual", "#sms-restore"].includes(location.hash)) return;
    channel = event.ports[0];
    const port = channel;
    port.onmessage = ({data}) => {
      if (channel !== port || !pending || typeof data !== "string" || data.length > 512) return;
      try {
        finish(JSON.parse(data));
      } catch {
        finish({state: "unavailable"});
      }
    };
    section.hidden = false;
    status.textContent = previous;
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!channel || pending || !event.isTrusted) return;
    if (from.value && through.value && from.value > through.value) {
      status.textContent = messages.invalid_range;
      return;
    }
    if (!csrf) {
      status.textContent = messages.unauthorized;
      return;
    }
    const stage = event.submitter === commitButton ? "commit" : "preview";
    if (stage === "commit" && !preview) return;
    const values = {csrf, from: from.value, through: through.value, stage, preview: stage === "commit" ? preview : ""};
    setPending(stage);
    status.textContent = stage === "preview" ? text("Vorschau wird erstellt …", "Preparing preview …")
      : text("Auswahl wird geprüft …", "Checking previewed messages …");
    try {
      channel.postMessage(JSON.stringify(values));
      timer = setTimeout(() => finish({state: "unavailable"}), 120000);
    } catch {
      finish({state: "unavailable"});
    }
  });

  for (const date of [from, through]) {
    date.addEventListener("input", () => {
      if (!preview) return;
      dropPreview();
      status.textContent = messages.preview_expired;
    });
  }

  window.addEventListener("pagehide", () => {
    clearTimeout(timer);
    channel?.close();
    channel = null;
    setPending("");
    dropPreview();
  });
})();
