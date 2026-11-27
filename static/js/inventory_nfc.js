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

  window.SimpleOfficeInventoryNfc = Object.freeze({normalizeUid, scan});
})();
