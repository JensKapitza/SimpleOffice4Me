/* User-initiated local preview only. Saving remains a normal explicit form. */
(() => {
  'use strict';
  const root = document.querySelector('[data-android-motion]');
  if (!root || window.top !== window) return;
  const csrf = root.querySelector('[data-motion-csrf]').value;
  const status = root.querySelector('[data-motion-status]');
  const buttons = Array.from(root.querySelectorAll('[data-motion-kind]'));
  const form = root.querySelector('[data-motion-save]');
  const channel = form?.elements.namedItem('channel_id');
  const save = form?.querySelector('button[type="submit"]');
  const units = { acceleration: 'm/s²', angular_speed: 'rad/s' };
  let binding, pending, preview;
  const defaultText = status.textContent;
  const show = text => { status.textContent = text; };
  function resetValue() {
    preview = undefined;
    if (!form) return;
    form.elements.namedItem('kind').value = '';
    form.elements.namedItem('value').value = '';
    channel.disabled = true;
    channel.value = '';
    save.disabled = true;
  }
  function buttonsReady() {
    for (const b of buttons) b.disabled = !binding?.available[b.dataset.motionKind] || Boolean(pending);
  }
  function clear() {
    binding = undefined;
    pending = undefined;
    resetValue();
    buttonsReady();
    show(defaultText);
  }
  function updateSave() {
    if (save) save.disabled = !preview || channel.selectedOptions[0]?.dataset.unit !== units[preview];
  }
  for (const b of buttons) b.addEventListener('click', e => {
    if (!e.isTrusted || document.hidden || !binding || pending || b.disabled) return;
    resetValue();
    pending = b.dataset.motionKind;
    buttonsReady();
    show('Einzelmessung wird gelesen …');
    try {
      if (window.SimpleOfficeAndroid.requestMotion(binding.token, binding.nonce, pending, csrf) === 'pending') return;
    } catch (_unavailable) {}
    pending = undefined;
    buttonsReady();
    show('Messung konnte nicht gestartet werden.');
  });
  channel?.addEventListener('change', updateSave);
  form?.addEventListener('submit', e => {
    if (!e.isTrusted || !binding || document.hidden || save.disabled) {
      e.preventDefault();
      return;
    }
    save.disabled = true;
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clear(); });
  window.addEventListener('pagehide', clear);
  window.SimpleOfficeAndroidMotion = Object.freeze({
    bind(token, nonce, acceleration, angularSpeed) {
      if (document.hidden) return;
      clear();
      binding = {token, nonce, available: {acceleration, angular_speed: angularSpeed}};
      buttonsReady();
      const yes = (has) => has ? 'verfügbar' : 'nicht verfügbar';
      show('Beschleunigung: ' + yes(acceleration) + '; Drehgeschwindigkeit: ' + yes(angularSpeed) + '.');
    },
    clear,
    result(nonce, kind, outcome, value) {
      if (!binding || binding.nonce !== nonce || kind !== pending || document.hidden) return;
      pending = undefined;
      buttonsReady();
      if (outcome !== 'captured' || typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
        show(outcome === 'blocked' ? 'Sitzung oder Berechtigung ist nicht mehr gültig.'
          : 'Kein frischer Messwert. Starte bei Bedarf eine neue Einzelmessung.');
        return;
      }
      show(new Intl.NumberFormat('de-DE', {maximumFractionDigits: 4}).format(value) + ' ' + units[kind]
          + ' · Noch nicht gespeichert.');
      if (form) {
        preview = kind;
        form.elements.namedItem('kind').value = kind;
        form.elements.namedItem('value').value = String(value);
        for (const o of channel.options) if (o.value) o.disabled = o.dataset.unit !== units[kind];
        channel.disabled = false;
      }
    }
  });
})();
