'use strict';

(() => {
  let sequence = 0;
  let readerReserved = false;
  const normalizeUid = (value) => {
    if (typeof value !== 'string' || value.length > 95) return '';
    const trimmed = value.trim();
    if (!/^[0-9a-f]{2}(?:[ :\-]?[0-9a-f]{2}){0,31}$/i.test(trimmed)) return '';
    return trimmed.replace(/[ :\-]/g, '').toUpperCase();
  };
  const requestMatches = async ({endpoint, token, uid, signal}) => {
    const reply = await fetch(endpoint, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      referrerPolicy: 'no-referrer', signal, headers: {
        'Content-Type': 'application/json', 'X-CSRF-Token': token,
      }, body: JSON.stringify({uid}),
    });
    if (!reply.ok) throw new Error('lookup unavailable');
    const data = await reply.json();
    if (!Array.isArray(data.matches) || data.matches.length > 20) throw new Error('invalid lookup');
    return data;
  };
  const scanFailureText = (code, english, timedOut = false) => {
    const messages = {
      unavailable: ['No direct NFC reader is available. Enter the tag ID or use a USB reader in keyboard mode.',
                    'Kein NFC-Direktleser verfügbar. Gib die Tag-Kennung ein oder verwende einen USB-Leser im Tastaturmodus.'],
      disabled: ['Enable NFC in Android settings and try again.', 'NFC in den Android-Einstellungen einschalten und erneut versuchen.'],
      denied: ['NFC permission was denied in the browser. You can enter the ID instead.',
               'Die NFC-Freigabe wurde im Browser verweigert. Du kannst die Kennung stattdessen eingeben.'],
      busy: ['Another NFC scan is already active.', 'Ein anderer NFC-Scan läuft bereits.'],
      'no-id': ['This tag did not provide an NFC ID.', 'Dieser Tag hat keine NFC-ID geliefert.'],
      timeout: ['No tag read. Select NFC to try again.', 'Kein Tag gelesen. Zum Wiederholen erneut auf NFC klicken.'],
      abort: ['NFC scan cancelled.', 'NFC-Scan abgebrochen.'],
      failed: ['Could not read the NFC ID. You can enter it instead.',
               'NFC-ID konnte nicht gelesen werden. Du kannst die Kennung stattdessen eingeben.'],
    };
    const key = code === 'abort' && timedOut ? 'timeout' : code;
    return (Object.hasOwn(messages, key) ? messages[key] : messages.failed)[english ? 0 : 1];
  };
  const browserFailure = (reason) => {
    switch (reason?.name) {
      case 'NotAllowedError':
      case 'SecurityError': return 'denied';
      case 'NotSupportedError': return 'unavailable';
      case 'AbortError': return 'abort';
      default: return 'failed';
    }
  };

  const scan = ({signal}) => new Promise((resolve, reject) => {
    const adapter = window.SimpleOfficeNativeNfc;
    const Reader = window.NDEFReader;
    const error = (code) => Object.assign(new Error(code), {code});
    if (signal.aborted) { reject(error('abort')); return; }
    if (!adapter && typeof Reader !== 'function') { reject(error('unavailable')); return; }
    if (readerReserved) { reject(error('busy')); return; }
    readerReserved = true;
    const requestId = 'inventory-' + Date.now().toString(36) + '-' + (++sequence).toString(36);
    const controller = new AbortController();
    let reader;
    let settled = false;
    const finish = (reason, serial) => {
      if (settled) return;
      settled = true;
      readerReserved = false;
      window.removeEventListener('simpleoffice:nfc', onNative);
      signal.removeEventListener('abort', onAbort);
      reader?.removeEventListener('reading', onReading);
      reader?.removeEventListener('readingerror', onError);
      controller.abort();
      if (adapter) {
        try { adapter.stop(requestId); } catch (_error) { /* Native has its own timeout. */ }
      }
      const uid = normalizeUid(serial);
      if (reason || !uid) reject(error(reason || 'no-id')); else resolve(uid);
    };
    const onAbort = () => finish('abort');
    const onReading = (event) => finish(null, event.serialNumber);
    const onError = () => finish('failed');
    const onNative = (event) => {
      const detail = event.detail;
      if (detail?.requestId === requestId) {
        finish(detail.state === 'ok' ? null : detail.state || 'failed', detail.serial);
      }
    };
    signal.addEventListener('abort', onAbort, {once: true});
    try {
      if (adapter) {
        window.addEventListener('simpleoffice:nfc', onNative);
        const state = adapter.start(requestId);
        if (state !== 'ok') finish(state || 'failed');
      } else {
        reader = new Reader();
        reader.addEventListener('reading', onReading);
        reader.addEventListener('readingerror', onError);
        Promise.resolve(reader.scan({signal: controller.signal})).catch((err) => {
          finish(browserFailure(err));
        });
      }
    } catch (failure) {
      finish(adapter ? 'failed' : browserFailure(failure));
    }
  });

  const bindControls = ({nfc, nfcButton, keyboardButton, form, page, english: EN, setStatus, duplicateProbe}) => {
    if (!nfc) return null;
    if (nfcButton) nfcButton.disabled = false;
    if (keyboardButton) keyboardButton.disabled = false;
    const STR = {
      nfcHold: EN ? 'Hold the NFC tag to the phone. Select NFC again to cancel.' : 'NFC-Tag ans Handy halten. Zum Abbrechen erneut auf NFC klicken.',
      nfcRead: EN ? 'NFC ID entered. Review and save the form.' : 'NFC-ID übernommen. Formular prüfen und speichern.',
      nfcKeyboard: EN ? 'Hold the tag to the USB reader. Its ID appears in this field.' : 'Tag an den USB-Leser halten. Seine UID erscheint in diesem Feld.',
      nfcKeyboardInvalid: EN ? 'No valid ID. Set the USB reader to send the tag ID in hexadecimal.' : 'Ungültige ID. USB-Leser so einstellen, dass er die Tag-UID als Hexadezimalwert ausgibt.',
    };
    let activeNfcScan = null;
    let keyboardBackup = null;
    const cancelNfc = (quiet = false) => {
      if (!activeNfcScan) return;
      activeNfcScan.quiet = quiet;
      activeNfcScan.controller.abort();
    };
    const applyNfc = (uid) => {
      nfc.value = uid;
      nfc.dispatchEvent(new Event('input', {bubbles: true}));
      setStatus(STR.nfcRead, 'success');
      duplicateProbe(uid, 'nfc');
    };
    const finishKeyboard = () => {
      const original = keyboardBackup;
      keyboardBackup = null;
      if (nfc.value === original) return;
      const uid = normalizeUid(nfc.value);
      if (uid) applyNfc(uid);
      else {
        nfc.value = original;
        setStatus(STR.nfcKeyboardInvalid, 'warning');
      }
    };
    keyboardButton?.addEventListener('click', () => {
      cancelNfc(true);
      if (keyboardBackup === null) keyboardBackup = nfc.value;
      nfc.focus();
      nfc.select();
      setStatus(STR.nfcKeyboard, 'primary');
    });
    nfc?.addEventListener('blur', () => {
      if (keyboardBackup !== null) finishKeyboard();
      else duplicateProbe(nfc.value, 'nfc');
    });
    nfc?.addEventListener('keydown', (event) => {
      // USB keyboard readers often append Enter. Never submit the object from this field.
      if (event.key !== 'Enter' || event.isComposing) return;
      event.preventDefault();
      if (keyboardBackup !== null) finishKeyboard();
      else duplicateProbe(nfc.value, 'nfc');
    });
    nfcButton?.addEventListener('click', async () => {
      if (activeNfcScan) { cancelNfc(); return; }
      const controller = new AbortController();
      const session = {controller, quiet: false};
      activeNfcScan = session;
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
      nfcButton.setAttribute('aria-busy', 'true');
      setStatus(STR.nfcHold, 'primary');
      try {
        applyNfc(await scan({signal: controller.signal}));
      } catch (error) {
        if (!session.quiet) {
          setStatus(scanFailureText(error?.code, EN, timedOut), 'warning');
          if (error?.code === 'unavailable') nfc.focus();
        }
      } finally {
        clearTimeout(timer);
        activeNfcScan = null;
        nfcButton.removeAttribute('aria-busy');
      }
    });
    page.addEventListener('visibilitychange', () => { if (page.hidden) cancelNfc(); });

    form?.addEventListener('submit', () => {
      if (keyboardBackup !== null) finishKeyboard();
      cancelNfc(true);
    }, {capture: true});
    return {cancel: cancelNfc, isScanning: () => activeNfcScan !== null};
  };

  const bindFinder = (page) => {
    const panel = page.getElementById('inventory-nfc-find');
    if (!panel || !panel.dataset.endpoint || !panel.dataset.detail) return;
    const form = panel.querySelector('form');
    const uidField = panel.querySelector('#nfc-find-uid');
    const button = panel.querySelector('[data-nfc-find-scan]');
    const status = panel.querySelector('[data-nfc-find-status]');
    const results = panel.querySelector('[data-nfc-find-results]');
    const token = form?.querySelector('input[name="_csrf_token"]')?.value;
    if (!token || !uidField || !status || !results || !button) return;
    const english = page.documentElement.lang === 'en';
    let lookup, scanning;
    const speak = (en, de) => { status.textContent = english ? en : de; };
    const clear = () => { lookup?.abort(); lookup = undefined; results.replaceChildren(); };
    const cancelScan = () => {
      const old = scanning;
      scanning = undefined;
      if (old) {
        button.removeAttribute('aria-busy');
        old.abort();
      }
    };

    const find = async () => {
      clear();
      const uid = normalizeUid(uidField.value);
      if (!uid) { speak('Enter a valid hexadecimal tag ID.', 'Gib eine gültige Hex-Tag-Kennung ein.'); return; }
      uidField.value = uid;
      const own = new AbortController();
      lookup = own;
      speak('Looking for the tag…', 'Tag wird gesucht…');
      const timeout = setTimeout(() => own.abort(), 8000);
      try {
        const data = await requestMatches({endpoint: panel.dataset.endpoint, token, uid, signal: own.signal});
        if (lookup !== own) return;
        const rows = data.matches.map(item => {
          if (!item || typeof item.object_id !== 'string' || !/^[0-9a-f-]{36}$/i.test(item.object_id)) {
            throw new Error('invalid item');
          }
          const entry = page.createElement('li');
          entry.className = 'list-group-item';
          const link = page.createElement('a');
          link.href = panel.dataset.detail.replace('__OBJECT__', item.object_id);
          link.textContent = [item.display_id ? '#' + item.display_id : '', item.name, item.location].filter(Boolean).join(' · ');
          entry.append(link);
          return entry;
        });
        results.replaceChildren(...rows);
        if (!rows.length) speak('No item has this tag.', 'Kein Gegenstand mit diesem Tag gefunden.');
        else if (data.limited) speak('More than 20 items share this tag.', 'Mehr als 20 Gegenstände haben diesen Tag.');
        else speak(rows.length + ' items found. Select an entry to open it.',
                   rows.length + ' Gegenstände gefunden. Wähle einen Eintrag zum Öffnen.');
      } catch (_error) {
        if (lookup === own) speak('The tag lookup is unavailable.', 'Die Tag-Suche ist nicht verfügbar.');
      } finally {
        clearTimeout(timeout);
        if (lookup === own) lookup = undefined;
      }
    };
    form.addEventListener('submit', event => { event.preventDefault(); cancelScan(); void find(); });
    uidField.addEventListener('input', () => { cancelScan(); clear(); status.textContent = ''; });
    const refreshReader = () => { button.hidden = !window.SimpleOfficeNativeNfc && typeof window.NDEFReader !== 'function'; };
    refreshReader();
    window.addEventListener('simpleoffice:native-ready', () => queueMicrotask(refreshReader));
    button.addEventListener('click', async () => {
      if (scanning) { cancelScan(); status.textContent = scanFailureText('abort', english); return; }
      clear();
      const own = new AbortController();
      scanning = own;
      let timedOut = false;
      button.setAttribute('aria-busy', 'true');
      const timeout = setTimeout(() => { timedOut = true; own.abort(); }, 15000);
      speak('Hold the tag near the device. Click again to cancel.', 'Tag ans Gerät halten. Erneut klicken zum Abbrechen.');
      try {
        const uid = await scan({signal: own.signal});
        if (scanning === own && !page.hidden) { uidField.value = uid; void find(); }
      } catch (error) {
        if (scanning === own && !page.hidden) {
          status.textContent = scanFailureText(error?.code, english, timedOut);
          if (error?.code === 'unavailable') uidField.focus();
        }
      } finally {
        clearTimeout(timeout);
        if (scanning === own) {
          scanning = undefined;
          button.removeAttribute('aria-busy');
        }
      }
    });
    page.addEventListener('visibilitychange', () => {
      if (page.hidden) {
        if (scanning) status.textContent = scanFailureText('abort', english);
        cancelScan();
        lookup?.abort();
        lookup = undefined;
      }
    });
    window.addEventListener('pagehide', () => { cancelScan(); lookup?.abort(); lookup = undefined; });
    panel.hidden = false;
  };

  window.SimpleOfficeInventoryNfc = Object.freeze({normalizeUid, scan, bindControls, requestMatches});
  if (typeof document !== 'undefined') bindFinder(document);
})();
