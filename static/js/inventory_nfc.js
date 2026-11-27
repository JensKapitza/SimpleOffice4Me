'use strict';

(() => {
  let sequence = 0;
  const normalizeUid = (value) => {
    if (typeof value !== 'string' || value.length > 95) return '';
    const trimmed = value.trim();
    if (!/^[0-9a-f]{2}(?:[ :\-]?[0-9a-f]{2}){0,31}$/i.test(trimmed)) return '';
    return trimmed.replace(/[ :\-]/g, '').toUpperCase();
  };
  const scan = ({signal}) => new Promise((resolve, reject) => {
    const adapter = window.SimpleOfficeNativeNfc;
    const Reader = window.NDEFReader;
    const error = (code) => Object.assign(new Error(code), {code});
    if (signal.aborted) { reject(error('abort')); return; }
    if (!adapter && typeof Reader !== 'function') { reject(error('unavailable')); return; }
    const requestId = 'inventory-' + Date.now().toString(36) + '-' + (++sequence).toString(36);
    const controller = new AbortController();
    let reader;
    let settled = false;
    const finish = (reason, serial) => {
      if (settled) return;
      settled = true;
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
          finish(err?.name === 'NotAllowedError' ? 'denied' : 'failed');
        });
      }
    } catch (_error) {
      finish('failed');
    }
  });

  const bindControls = ({nfc, nfcButton, keyboardButton, form, page, english: EN, setStatus, duplicateProbe}) => {
    if (!nfc) return null;
    if (nfcButton) nfcButton.disabled = false;
    if (keyboardButton) keyboardButton.disabled = false;
    const STR = {
      nfcUnsupported: EN ? 'No NFC scan available here. Use the Android app, a USB NFC reader in keyboard mode, or enter the ID.' : 'Hier ist kein NFC-Direktscan verfügbar. Android-App, USB-NFC-Leser im Tastaturmodus oder manuelle Eingabe verwenden.',
      nfcHold: EN ? 'Hold the NFC tag to the phone. Select NFC again to cancel.' : 'NFC-Tag ans Handy halten. Zum Abbrechen erneut auf NFC klicken.',
      nfcRead: EN ? 'NFC ID entered. Review and save the form.' : 'NFC-ID übernommen. Formular prüfen und speichern.',
      nfcFailed: EN ? 'Could not read the NFC ID.' : 'NFC-ID konnte nicht gelesen werden.',
      nfcDisabled: EN ? 'Enable NFC in Android settings and try again.' : 'NFC in den Android-Einstellungen einschalten und erneut versuchen.',
      nfcDenied: EN ? 'NFC permission was denied in the browser.' : 'NFC-Freigabe im Browser wurde verweigert.',
      nfcNoId: EN ? 'This tag did not provide an NFC ID.' : 'Dieser Tag hat keine NFC-ID geliefert.',
      nfcTimeout: EN ? 'No tag read. Select NFC to try again.' : 'Kein Tag gelesen. Zum Wiederholen erneut auf NFC klicken.',
      nfcCancelled: EN ? 'NFC scan cancelled.' : 'NFC-Scan abgebrochen.',
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
        const messages = {
          unavailable: STR.nfcUnsupported, disabled: STR.nfcDisabled, denied: STR.nfcDenied,
          'no-id': STR.nfcNoId, timeout: STR.nfcTimeout, abort: timedOut ? STR.nfcTimeout : STR.nfcCancelled,
        };
        if (!session.quiet) {
          const message = Object.hasOwn(messages, error?.code) ? messages[error.code] : STR.nfcFailed;
          setStatus(message, 'warning');
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

  window.SimpleOfficeInventoryNfc = Object.freeze({normalizeUid, scan, bindControls});
})();
