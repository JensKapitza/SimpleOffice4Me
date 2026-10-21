(() => {
  "use strict";

  const root = document.querySelector("[data-local-folder-page]");
  if (!root) return;

  const addButton = root.querySelector("#local-folder-add");
  const list = root.querySelector("#local-folder-list");
  const empty = root.querySelector("#local-folder-empty");
  const fallback = root.querySelector("#local-folder-fallback");
  const status = root.querySelector("#local-folder-status");
  const dbName = "simpleoffice-local-folders";
  const storeName = "folders";
  let androidBridge = null;
  let directoryPickerAvailable = typeof window.showDirectoryPicker === "function" && Boolean(window.indexedDB);

  const showStatus = (message, kind = "info") => {
    status.className = `alert alert-${kind}`;
    status.textContent = message;
    status.hidden = false;
  };

  const openDatabase = () => new Promise((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(storeName)) {
        database.createObjectStore(storeName, {keyPath: "id"});
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Lokaler Speicher ist nicht verfügbar."));
  });

  const withObjectStore = async (mode, operation) => {
    const database = await openDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction(storeName, mode);
        const request = operation(transaction.objectStore(storeName));
        let result;
        request.onsuccess = () => { result = request.result; };
        request.onerror = () => reject(request.error || new Error("Lokale Ordnerdaten konnten nicht gelesen werden."));
        transaction.oncomplete = () => resolve(result);
        transaction.onerror = () => reject(transaction.error || new Error("Lokale Ordnerdaten konnten nicht gespeichert werden."));
        transaction.onabort = () => reject(transaction.error || new Error("Lokale Ordnerdaten konnten nicht gespeichert werden."));
      });
    } finally {
      database.close();
    }
  };

  const browserEntries = () => withObjectStore("readonly", (store) => store.getAll());
  const saveBrowserEntry = (entry) => withObjectStore("readwrite", (store) => store.put(entry));
  const removeBrowserEntry = (id) => withObjectStore("readwrite", (store) => store.delete(id));

  const browserStatus = async (handle, probeFolder = false) => {
    try {
      if (await handle.queryPermission({mode: "read"}) !== "granted") return "permission_revoked";
      if (probeFolder) await handle.entries().next();
      return "available";
    } catch (error) {
      return error?.name === "NotFoundError" ? "unavailable" : "permission_revoked";
    }
  };

  const statusLabel = (value) => ({
    available: "Verbindung verfügbar",
    permission_saved: "Berechtigung gespeichert · noch nicht geprüft",
    permission_revoked: "Zugriff nicht freigegeben",
    unavailable: "Ordner nicht erreichbar",
  })[value] || "Status unbekannt";

  const importBrowserFolder = async (entry) => {
    if (await entry.handle.requestPermission({mode: "read"}) !== "granted") {
      showStatus("Der Browserzugriff auf den Ordner wurde nicht freigegeben.", "warning");
      return;
    }
    const upload = new FormData();
    upload.append("_csrf_token", root.dataset.csrfToken || "");
    let fileCount = 0;
    let totalBytes = 0;
    for await (const [name, handle] of entry.handle.entries()) {
      if (handle.kind !== "file") continue;
      if (fileCount >= 50) throw new Error("Je Import sind höchstens 50 Dateien zulässig.");
      const file = await handle.getFile();
      totalBytes += file.size;
      if (totalBytes > 256 * 1024 * 1024) throw new Error("Der Ordnerimport ist auf 256 MiB begrenzt.");
      upload.append("files", file, name);
      fileCount += 1;
    }
    if (!fileCount) {
      showStatus("Im ausgewählten Ordner wurden keine Dateien auf der obersten Ebene gefunden.", "info");
      return;
    }
    showStatus(`${fileCount} Datei(en) direkt aus diesem Ordner werden über den bestehenden, geschützten Import übernommen. Unterordner separat auswählen.`);
    const response = await fetch(root.dataset.uploadUrl, {
      method: "POST",
      body: upload,
      credentials: "same-origin",
      redirect: "follow",
    });
    if (!response.ok) throw new Error(`Import fehlgeschlagen (HTTP ${response.status}).`);
    if (response.redirected) {
      window.location.assign(response.url);
      return;
    }
    showStatus("Dateien wurden an den Dokumentenimport übergeben.", "success");
  };

  const importControl = (entry, native) => {
    if (!native) {
      const button = makeElement("button", "btn btn-sm btn-outline-primary", "Dateien importieren");
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await importBrowserFolder(entry);
        } catch (error) {
          showStatus(error?.message || "Ordnerdateien konnten nicht importiert werden.", "danger");
        } finally {
          button.disabled = false;
        }
      });
      return button;
    }

    const form = makeElement("form", "d-inline-flex flex-wrap align-items-center gap-2");
    form.method = "post";
    form.action = root.dataset.uploadUrl || "/documents/upload";
    form.enctype = "multipart/form-data";
    const csrf = makeElement("input");
    csrf.type = "hidden";
    csrf.name = "_csrf_token";
    csrf.value = root.dataset.csrfToken || "";
    const input = makeElement("input", "visually-hidden");
    input.type = "file";
    input.name = "files";
    input.multiple = true;
    input.id = `folder-files-${entry.id}`;
    input.addEventListener("click", (event) => {
      const result = androidBridge.prepareImport(entry.id);
      if (result !== "ready") {
        event.preventDefault();
        const message = result === "permission-revoked"
          ? "Ordnerzugriff wurde widerrufen. Ändere den Ordner oder erteile die Freigabe erneut."
          : result === "initial-folder-unsupported"
            ? "Diese Android-Version kann die Dateiauswahl nicht im gespeicherten Ordner öffnen. Nutze den normalen Dateiimport."
            : "Dateiauswahl aus diesem Ordner ist nicht verfügbar.";
        showStatus(message, "warning");
      }
    });
    const label = makeElement("label", "btn btn-sm btn-outline-primary mb-0", "Dateien aus Ordner wählen");
    label.htmlFor = input.id;
    const submit = makeElement("button", "btn btn-sm btn-primary", "Importieren");
    submit.type = "submit";
    submit.hidden = true;
    input.addEventListener("change", () => {
      submit.hidden = !input.files || input.files.length === 0;
      if (input.files?.length) showStatus(`${input.files.length} Datei(en) ausgewählt. Beim Import werden die Dateien kopiert; das Original im Ordner bleibt erhalten.`);
    });
    form.append(csrf, input, label, submit);
    return form;
  };

  const makeElement = (tag, className, text = "") => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  };

  const renderEntry = (entry, native) => {
    const card = makeElement("article", "card h-100");
    const body = makeElement("div", "card-body");
    const heading = makeElement("h2", "h5", entry.name || "Lokaler Ordner");
    const type = makeElement("p", "small text-secondary mb-1", native ? "Android-Systemordner (SAF)" : "Browserordner (lokal)");
    const state = makeElement("p", "small text-secondary", statusLabel(entry.status));
    body.append(heading, type, state);
    if (entry.description) body.append(makeElement("p", "small", entry.description));

    const actions = makeElement("div", "d-flex flex-wrap gap-2");
    const check = makeElement("button", "btn btn-sm btn-outline-primary", "Verbindung prüfen");
    check.type = "button";
    check.addEventListener("click", async () => {
      if (native) {
        entry = androidBridge.check(entry.id);
      } else {
        const permission = await entry.handle.requestPermission({mode: "read"});
        entry.status = permission === "granted" ? await browserStatus(entry.handle, true) : "permission_revoked";
      }
      await refresh();
      showStatus(statusLabel(entry.status), entry.status === "available" ? "success" : "warning");
    });

    const change = makeElement("button", "btn btn-sm btn-outline-secondary", "Ordner ändern");
    change.type = "button";
    change.addEventListener("click", () => chooseFolder(entry.id));

    const edit = makeElement("button", "btn btn-sm btn-outline-secondary", "Name / Beschreibung");
    edit.type = "button";
    edit.addEventListener("click", () => editEntry(entry, native));

    const remove = makeElement("button", "btn btn-sm btn-outline-danger", "Freigabe entfernen");
    remove.type = "button";
    remove.title = "Entfernt nur den gespeicherten Zugriff, keine Dateien.";
    remove.addEventListener("click", async () => {
      if (native) {
        const result = androidBridge.remove(entry.id);
        if (result !== "ok") return showStatus("Lokale Freigabe konnte nicht entfernt werden.", "danger");
      } else {
        await removeBrowserEntry(entry.id);
      }
      await refresh();
      showStatus("Die lokale Freigabe wurde entfernt. Ordnerdateien blieben unverändert.", "success");
    });
    actions.append(check, change, edit, remove);
    body.append(actions);
    body.append(makeElement("hr", "my-3"), importControl(entry, native));
    card.append(body);
    return card;
  };

  const editEntry = async (entry, native) => {
    const name = window.prompt("Anzeigename des Ordners", entry.name || "");
    if (name === null) return;
    const description = window.prompt("Beschreibung (optional)", entry.description || "");
    if (description === null) return;
    const cleanName = name.trim();
    const cleanDescription = description.trim();
    if (!cleanName || cleanName.length > 120 || cleanDescription.length > 500) {
      return showStatus("Bitte einen Namen (1–120 Zeichen) und eine optionale Beschreibung (max. 500 Zeichen) eingeben.", "warning");
    }
    if (native) {
      try {
        androidBridge.rename(entry.id, cleanName, cleanDescription);
      } catch (error) {
        return showStatus("Ordnername konnte nicht gespeichert werden.", "danger");
      }
    } else {
      entry.name = cleanName;
      entry.description = cleanDescription;
      await saveBrowserEntry(entry);
    }
    await refresh();
    showStatus("Anzeigename und Beschreibung wurden lokal gespeichert.", "success");
  };

  const refresh = async () => {
    list.replaceChildren();
    try {
      const native = Boolean(androidBridge);
      const entries = native ? androidBridge.list() : await browserEntries();
      for (const entry of entries) {
        if (!native) entry.status = await browserStatus(entry.handle);
        const column = makeElement("div", "col-12 col-lg-6");
        column.append(renderEntry(entry, native));
        list.append(column);
      }
      empty.hidden = entries.length > 0;
      const canChooseFolder = native || directoryPickerAvailable;
      addButton.disabled = !canChooseFolder;
      addButton.textContent = canChooseFolder ? "Lokalen Ordner hinzufügen" : "Ordnerauswahl nicht unterstützt";
      fallback.hidden = canChooseFolder;
    } catch (error) {
      showStatus("Lokale Ordner konnten nicht gelesen werden.", "danger");
    }
  };

  const chooseFolder = async (existingId = "") => {
    if (androidBridge) {
      const result = androidBridge.choose(existingId);
      if (result !== "pending") showStatus(result === "busy" ? "Eine Ordnerauswahl läuft bereits." : "Android-Ordnerauswahl ist nicht verfügbar.", "warning");
      else showStatus("Bitte einen Ordner im Android-Systemdialog auswählen.");
      return;
    }
    if (!directoryPickerAvailable) {
      fallback.hidden = false;
      return;
    }
    try {
      const handle = await window.showDirectoryPicker({mode: "read"});
      const current = existingId ? (await browserEntries()).find((item) => item.id === existingId) : null;
      const id = existingId || (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
      await saveBrowserEntry({id, name: handle.name, description: current?.description || "", handle});
      await refresh();
      showStatus("Ordnerzugriff ist lokal auf diesem Gerät gespeichert.", "success");
    } catch (error) {
      if (error?.name !== "AbortError") showStatus("Der ausgewählte Ordner konnte nicht gespeichert werden.", "warning");
    }
  };

  addButton.addEventListener("click", () => chooseFolder());

  window.addEventListener("simpleoffice:native-ready", () => {
    if (window.SimpleOfficeLocalFolders) {
      androidBridge = window.SimpleOfficeLocalFolders;
      directoryPickerAvailable = false;
      refresh();
    }
  });
  window.addEventListener("simpleoffice:local-folder-result", async (event) => {
    const result = event.detail || {};
    await refresh();
    showStatus(result.message || "Ordnerauswahl abgeschlossen.", result.state === "selected" ? "success" : "warning");
  });

  refresh();
})();
