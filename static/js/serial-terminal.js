(() => {
  "use strict";
  const root = document.querySelector("[data-serial-terminal]");
  if (!root || root.dataset.local !== "1") return;
  const by = (name) => root.querySelector(`[data-${name}]`);
  const status = (message) => { by("status").textContent = message; };
  const limitBytes = 4 * 1024 * 1024;
  const limitRecords = 8192;
  const endings = new Map([["none", ""], ["lf", "\n"], ["cr", "\r"], ["crlf", "\r\n"]]);
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(window.location?.hostname);
  const serial = window.isSecureContext && window.top === window && loopback && navigator.serial;
  const channels = by("sample-form");
  let session = null;
  let closedPage = false;
  let capturing = false;
  let records = [];
  let capturedBytes = 0;
  let line = "";
  let skipLine = false;
  let preview = "";
  let frame = null;
  let sample = null;

  if (!serial || typeof serial.requestPort !== "function") {
    status("Dieser Browser stellt keinen USB-/COM-Port bereit. Für dieses Terminal öffne SimpleOffice lokal in einem Desktop-Browser mit Web Serial, etwa Chrome.");
    return;
  }
  const refreshControls = () => {
    const active = session?.state === "open";
    by("settings").disabled = !!session || closedPage;
    by("disconnect").disabled = !active;
    by("send").disabled = !active || !!session?.writer;
    by("capture").disabled = !active;
    by("export").disabled = records.length === 0;
    by("clear").disabled = capturing || records.length === 0;
    if (channels) by("save").disabled = sample === null;
  };

  function portOptions() {
    const baudRate = Number(by("baud").value);
    if (!Number.isInteger(baudRate) || baudRate < 50 || baudRate > 3000000) return null;
    return {baudRate, dataBits: Number(by("bits").value), stopBits: Number(by("stopbits").value),
      parity: by("parity").value, flowControl: by("flow").value, bufferSize: 4096};
  }

  function sampleLines(chunk) {
    for (const char of chunk) {
      if (char === "\n" || char === "\r") {
        const prefix = /^SO4M,([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)$/i.exec(line);
        if (!skipLine && prefix && Number.isFinite(Number(prefix[1]))) {
          sample = Number(prefix[1]);
          by("value").textContent = String(sample);
          refreshControls();
        }
        line = "";
        skipLine = false;
      } else if (!skipLine) {
        if (line.length === 80) { line = ""; skipLine = true; }
        else line += char;
      }
    }
  }

  function previewIncoming(bytes) {
    const chunk = decoder.decode(bytes, {stream: true});
    sampleLines(chunk);
    preview = (preview + chunk.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "�")).slice(-24000);
    if (frame !== null) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      const output = by("output");
      const follow = output.scrollHeight - output.clientHeight - output.scrollTop < 20;
      const top = output.scrollTop;
      output.value = preview;
      output.scrollTop = follow ? output.scrollHeight : top;
    });
  }

  function setCapture(active, message) {
    capturing = active;
    by("capture").textContent = active ? "Mitschnitt stoppen" : "Mitschnitt starten";
    by("capture-status").textContent = message;
    refreshControls();
  }

  function record(direction, bytes) {
    if (!capturing || bytes.length === 0) return;
    if (capturedBytes + bytes.length > limitBytes || records.length >= limitRecords) {
      setCapture(false, "Mitschnittgrenze erreicht. Speichere den Mitschnitt; der Port bleibt verbunden.");
      return;
    }
    let binary = "";
    for (let index = 0; index < bytes.length; index += 4096) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 4096));
    }
    capturedBytes += bytes.length;
    records.push(JSON.stringify({time: new Date().toISOString(), direction, base64: btoa(binary)}) + "\n");
    if (records.length === 1) refreshControls();
  }

  async function close(current, message) {
    if (current !== session || current.state === "closing") return;
    current.state = "closing";
    if (capturing) setCapture(false, "Mitschnitt beendet. Du kannst ihn jetzt speichern.");
    refreshControls();
    if (current.reader) {
      try { await current.reader.cancel(); } catch { /* The stream may already have failed. */ }
    }
    if (current.writer) {
      try { await current.writer.abort(); } catch { /* The stream may already have failed. */ }
    }
    await Promise.allSettled([current.reading, current.writing]);
    try {
      await current.port?.close();
    } catch {
      message = "Port nicht sauber geschlossen. Den Adapter bei Bedarf neu verbinden.";
    } finally {
      decoder.decode();
      line = "";
      skipLine = false;
      if (session === current) session = null;
      refreshControls();
      status(message);
    }
  }

  async function read(current) {
    const reader = current.port.readable?.getReader();
    if (!reader) throw new Error("unavailable");
    current.reader = reader;
    try {
      while (current.state === "open") {
        const next = await reader.read();
        if (next.done || current.state !== "open") break;
        record("rx", next.value);
        previewIncoming(next.value);
      }
    } finally {
      current.reader = null;
      reader.releaseLock();
    }
  }

  by("connect").addEventListener("click", async () => {
    if (session || closedPage) return;
    const options = portOptions();
    if (!options) { status("Wähle eine gültige Baudrate (50 bis 3.000.000)."); return; }
    const current = {state: "opening"};
    session = current;
    refreshControls();
    status("Wähle den gewünschten Port im Browserdialog.");
    try {
      current.port = await serial.requestPort();
      if (current.cancelled) { session = null; refreshControls(); return; }
      await current.port.open(options);
    } catch (error) {
      session = null;
      refreshControls();
      status(error?.name === "NotFoundError" ? "Keine Portauswahl." : "Port konnte nicht geöffnet werden. Prüfe Zugriff, Einstellungen und ob ein anderes Programm den Port verwendet.");
      return;
    }
    if (current.cancelled) { void close(current, "Port getrennt."); return; }
    current.state = "open";
    sample = null;
    by("value").textContent = "–";
    refreshControls();
    status("Port verbunden.");
    current.reading = read(current);
    current.reading.then(
      () => { void close(current, "Port getrennt."); },
      () => {
        current.reading = null;
        void close(current, "Empfang unterbrochen. Prüfe den Adapter oder verbinde den Port erneut.");
      }
    );
  });

  by("disconnect").addEventListener("click", () => { if (session) void close(session, "Port getrennt."); });
  serial.addEventListener("disconnect", (event) => {
    if (session?.state === "open" && event.target === session.port) void close(session, "Adapter getrennt.");
  });
  by("send-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const current = session;
    if (current?.state !== "open" || current.writer) return;
    const input = by("input");
    if (!input.value || input.value.length > 8192) { status("Gib eine Nachricht mit maximal 8192 Zeichen ein."); return; }
    let bytes;
    if (by("format").value === "hex") {
      const hex = input.value.replace(/\s+/g, "");
      if (!/^(?:[0-9a-f]{2})+$/i.test(hex)) { status("Hex-Eingabe benötigt vollständige Bytepaare, zum Beispiel 0D 0A."); return; }
      bytes = Uint8Array.from(hex.match(/../g), (value) => Number.parseInt(value, 16));
    } else {
      bytes = encoder.encode(input.value + endings.get(by("ending").value));
    }
    let writer;
    try {
      writer = current.port.writable?.getWriter();
    } catch {
      status("Port ist gerade nicht zum Senden verfügbar.");
      return;
    }
    if (!writer) { status("Port ist nicht zum Senden verfügbar."); return; }
    current.writer = writer;
    refreshControls();
    current.writing = (async () => {
      try {
        await writer.write(bytes);
        record("tx", bytes);
        if (current.state === "open") status(`${bytes.length} Byte gesendet.`);
      } catch {
        if (current.state === "open") status("Senden fehlgeschlagen. Prüfe den Port.");
      } finally {
        writer.releaseLock();
        current.writer = null;
        refreshControls();
      }
    })();
  });

  by("capture").addEventListener("click", () => {
    if (session?.state !== "open") return;
    if (capturing) { setCapture(false, "Mitschnitt beendet. Du kannst ihn jetzt speichern."); return; }
    if (capturedBytes >= limitBytes || records.length >= limitRecords) {
      status("Speichere den Mitschnitt, um wieder Platz zu schaffen.");
      return;
    }
    setCapture(true, "Mitschnitt aktiv. Die Daten bleiben in diesem Browser-Tab.");
  });
  by("export").addEventListener("click", () => {
    if (!records.length) return;
    const url = URL.createObjectURL(new Blob(records, {type: "application/x-ndjson"}));
    const a = document.createElement("a");
    a.href = url;
    a.download = "simpleoffice-seriell.jsonl";
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (!capturing) setCapture(false, "Download angefordert. Der Mitschnitt bleibt bis zum Leeren im Tab.");
  });
  by("clear").addEventListener("click", () => {
    if (capturing || !records.length || !window.confirm("Mitschnitt in diesem Tab unwiderruflich leeren?")) return;
    records = [];
    capturedBytes = 0;
    setCapture(false, "Mitschnitt geleert.");
  });
  if (channels) {
    channels.addEventListener("submit", (event) => {
      if (sample === null) { event.preventDefault(); return; }
      channels.action = by("channel").value;
      by("sample").value = String(sample);
    });
  }
  window.addEventListener("pagehide", () => {
    closedPage = true;
    if (session) session.cancelled = true;
    if (session?.state === "open") void close(session, "Port getrennt.");
  });
  window.addEventListener("pageshow", () => {
    closedPage = false;
    refreshControls();
  });
  refreshControls();
})();
