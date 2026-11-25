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
  let androidBridge = window.SimpleOfficeLocalFolders || null;
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

  const panel = root.querySelector("#local-folder-search-panel");
  const names = root.querySelector("#local-folder-search-input");
  const nameList = root.querySelector("#local-folder-search-results");
  const nameStatus = root.querySelector("#local-folder-search-status");
  const reloadNames = root.querySelector("#local-folder-search-refresh");
  const english = document.documentElement.lang.toLowerCase().startsWith("en");
  const say = (de, en) => english ? en : de;
  let source = null;
  let indexed = [];
  let scanId = "";
  let openId = "";
  let requestTimer = 0;
  let limited = false;
  let needsRefresh = false;
  const canSearch = () => androidBridge && typeof androidBridge.scan === "function";
  const requestId = () => {
    if (typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    // Older WebViews may expose getRandomValues without randomUUID.
    const bytes = window.crypto.getRandomValues(new Uint8Array(16));
    const value = Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("");
    return [value.slice(0, 8), value.slice(8, 12), value.slice(12, 16), value.slice(16, 20), value.slice(20)].join("-");
  };

  const resetNameResults = () => {
    window.clearTimeout(requestTimer);
    indexed = [];
    scanId = "";
    openId = "";
    limited = false;
    if (panel) nameList.replaceChildren();
    if (canSearch()) androidBridge.clear();
  };

  const renderNames = () => {
    if (!source || !panel) return;
    nameList.replaceChildren();
    const query = names.value.trim().normalize("NFC").toLocaleLowerCase();
    const terms = query.split(/\s+/).filter(Boolean);
    const matches = indexed.filter((file) => {
      const key = file.name.normalize("NFC").toLocaleLowerCase();
      return terms.every((term) => key.includes(term));
    });
    for (const file of matches.slice(0, 60)) {
      const row = makeElement("li", "list-group-item d-flex flex-wrap align-items-center justify-content-between gap-2");
      const title = makeElement("div", "text-break");
      title.append(makeElement("strong", "", file.name));
      if (file.parent) title.append(makeElement("div", "small text-secondary", file.parent));
      const open = makeElement("button", "btn btn-sm btn-outline-primary", say("Datei öffnen", "Open file"));
      open.type = "button";
      open.setAttribute("aria-label", say(`Datei öffnen: ${file.name}`, `Open file: ${file.name}`));
      open.addEventListener("click", () => {
        if (openId) return;
        openId = requestId();
        if (androidBridge.open(file.id, openId) !== "pending") {
          openId = "";
          nameStatus.textContent = say("Datei nicht mehr verfügbar. Bitte neu einlesen.", "File is no longer available. Refresh the source.");
        }
      });
      row.append(title, open);
      nameList.append(row);
    }
    const found = matches.length
      ? (matches.length > 60 ? say("Erste 60 Treffer. Verfeinere deine Suche.", "First 60 matches. Refine your search.") :
        say(`${matches.length} Treffer aus „${source.name}“.`, `${matches.length} matches in “${source.name}”.`))
      : say("Keine passenden Dateinamen gefunden.", "No matching file names found.");
    nameStatus.textContent = found + (limited
      ? say(" Der Suchbereich ist begrenzt; wähle bei Bedarf einen kleineren Unterordner.",
            " The search is bounded; choose a smaller subfolder if necessary.") : "");
  };

  const startNameIndex = (entry, keepQuery = false) => {
    if (!canSearch() || !panel || !entry) return;
    resetNameResults();
    source = entry;
    needsRefresh = false;
    panel.hidden = false;
    if (!keepQuery) names.value = "";
    if (!entry.search_ready) {
      nameStatus.textContent = say("Wähle diesen früher gespeicherten Ordner erneut, um seine Dateinamen für dieses lokale Konto freizugeben.",
        "Re-select this previously saved folder to allow file-name search for this local account.");
      return;
    }
    scanId = requestId();
    nameStatus.textContent = say(`Dateinamen in „${entry.name}“ werden lokal eingelesen …`, `Reading file names in “${entry.name}” on this device …`);
    if (androidBridge.scan(entry.id, scanId) !== "pending") {
      scanId = "";
      nameStatus.textContent = say("Die lokale Suche konnte nicht gestartet werden.", "Local search could not start.");
      return;
    }
    requestTimer = window.setTimeout(() => {
      resetNameResults();
      nameStatus.textContent = say("Die Quelle antwortet nicht. Prüfe Gerät bzw. Verbindung und lies sie erneut ein.",
        "The source is not responding. Check the device or connection and refresh.");
    }, 12000);
  };

  const onNameResume = () => {
    if (needsRefresh && source && !document.hidden) startNameIndex(source, true);
  };
  if (panel) {
    names.addEventListener("input", () => { if (!scanId && indexed.length) renderNames(); });
    reloadNames.addEventListener("click", () => startNameIndex(source, true));
  }
  window.addEventListener("simpleoffice:folder-names", (event) => {
    const detail = event.detail || {};
    if (!scanId || detail.id !== scanId || document.hidden || !source) return;
    scanId = "";
    window.clearTimeout(requestTimer);
    if (detail.status !== "available") {
      resetNameResults();
      nameStatus.textContent = detail.status === "permission_revoked"
        ? say("Ordnerfreigabe fehlt. Wähle den Ordner erneut.", "Folder access is missing. Select it again.")
        : say("Ordner nicht erreichbar. Prüfe SD/USB, Provider oder Offline-Verfügbarkeit.", "Folder unavailable. Check SD/USB, its provider or offline availability.");
      return;
    }
    indexed = Array.isArray(detail.files) ? detail.files.slice(0, 400) : [];
    limited = detail.limited === true;
    renderNames();
  });
  window.addEventListener("simpleoffice:folder-open", (event) => {
    const detail = event.detail || {};
    if (!openId || detail.id !== openId || document.hidden) return;
    openId = "";
    if (detail.status === "chooser") return;
    resetNameResults();
    nameStatus.textContent = detail.status === "permission_revoked"
      ? say("Die Ordnerfreigabe wurde widerrufen. Wähle ihn erneut.", "Folder permission was revoked. Select it again.")
      : say("Datei nicht mehr erreichbar oder kein passender Viewer. Lies den Ordner erneut ein.", "File no longer reachable or no compatible viewer. Refresh the folder.");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && source) {
      resetNameResults();
      needsRefresh = true;
    } else onNameResume();
  });
  window.addEventListener("simpleoffice:folder-source-resume", () => {
    if (source) needsRefresh = true;
    onNameResume();
  });
  window.addEventListener("pagehide", resetNameResults);

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
      if (source && source.id === entry.id) {
        resetNameResults();
        source = null;
        panel.hidden = true;
      } else if (native && source) {
        startNameIndex(source, true);
      }
      await refresh();
      showStatus("Die lokale Freigabe wurde entfernt. Ordnerdateien blieben unverändert.", "success");
    });
    if (native && canSearch()) {
      const find = makeElement("button", "btn btn-sm btn-primary", say("Dateinamen durchsuchen", "Search file names"));
      find.type = "button";
      find.addEventListener("click", () => {
        startNameIndex(entry);
        names.focus();
      });
      actions.append(find);
    }
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
    if (result.state === "selected" && result.entry) startNameIndex(result.entry);
  });

  refresh();
})();
