/* Decode a pasted SO4ME1 packet and download it only after an explicit click. */
(() => {
  'use strict';
  const input = document.querySelector('[data-transfer-input]');
  const button = document.querySelector('[data-transfer-download]');
  const status = document.querySelector('[data-transfer-status]');
  if (!input || !button || !status) return;
  const isGerman = (document.documentElement.lang || '').toLowerCase().startsWith('de');
  const fail = () => { button.disabled = true; status.textContent = isGerman ? 'Kein gültiges Dateipaket erkannt.' : 'No valid file packet found.'; };
  let ready = null;
  let revision = 0;
  function parse() {
    const currentRevision = ++revision;
    ready = null;
    button.disabled = true;
    const raw = input.value.trim();
    if (!raw) { status.textContent = ''; return; }
    if (raw.length > 1800 || !raw.startsWith('SO4ME1|') || !/^[\x00-\x7f]+$/.test(raw)) { fail(); return; }
    const parts = raw.split('|');
    if (parts.length !== 5 || !/^[A-Za-z0-9._-]{1,80}$/.test(parts[1]) || !/^\d{1,4}$/.test(parts[2]) || !/^[a-f0-9]{64}$/.test(parts[3])) { fail(); return; }
    const size = Number(parts[2]);
    if (size > 1100 || parts[4].length > 1500) { fail(); return; }
    try {
      const binary = atob(parts[4]);
      if (binary.length !== size) { fail(); return; }
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      if (!globalThis.crypto?.subtle) {
        status.textContent = isGerman ? 'Zur Dateiprüfung diese Seite über HTTPS oder localhost öffnen.' : 'Open this page using HTTPS or localhost to verify the file.';
        return;
      }
      crypto.subtle.digest('SHA-256', bytes).then((hash) => {
        if (currentRevision !== revision || input.value.trim() !== raw) return;
        const digest = [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
        if (digest !== parts[3]) { fail(); return; }
        ready = { name: parts[1], bytes };
        button.disabled = false;
        status.textContent = isGerman ? `Datei erkannt: ${parts[1]} (${size} Bytes). Zum Speichern auf „Datei laden“ klicken.` : `File detected: ${parts[1]} (${size} bytes). Click “Download file” to save it.`;
      }).catch(() => { if (currentRevision === revision) fail(); });
    } catch { fail(); }
  }
  input.addEventListener('input', parse);
  document.querySelector('[data-acoustic-result]')?.addEventListener('input', (event) => {
    const value = event.currentTarget.value;
    if (value.startsWith('SO4ME1|')) { input.value = value; parse(); }
  });
  button.addEventListener('click', () => {
    if (!ready) return;
    const url = URL.createObjectURL(new Blob([ready.bytes], { type: 'application/octet-stream' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = ready.name;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
