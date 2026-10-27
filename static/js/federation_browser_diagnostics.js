(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SimpleOfficeFederationBrowserDiagnostics = api;
  if (root.document && typeof module !== "object") {
    const initialize = () => api.install(root.document, root);
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", initialize, {once: true});
    else initialize();
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const INVALID_URL_MESSAGE = "Client: nicht auswertbar (ungültige Peer-Adresse); kein Verbindungsnachweis";
  const UNVERIFIED_MESSAGE = "Client: Anfrage ausgelöst, Antwort nicht verifizierbar (CSP, CORS, Mixed Content, TLS oder Browser-Schutz); kein Erreichbarkeitsnachweis";
  const UNAVAILABLE_MESSAGE = "Client: nicht auswertbar (Netzwerk, Timeout, CSP, CORS, Mixed Content, TLS oder Browser-Schutz); Peer nicht zwingend offline";

  function safePeerUrl(value, URLConstructor) {
    let url;
    try {
      url = new URLConstructor(value);
    } catch (_) {
      return null;
    }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    return url;
  }

  function install(document, window) {
    const findStatus = (peerId) => Array.from(document.querySelectorAll('[data-peer-browser-status]'))
      .find((element) => element.dataset.peerBrowserStatus === peerId);

    document.querySelectorAll('[data-peer-open]').forEach((button) => {
      button.addEventListener('click', () => {
        const status = findStatus(button.dataset.peerOpen);
        const peerUrl = safePeerUrl(button.dataset.peerUrl, window.URL);
        if (!peerUrl) {
          if (status) status.textContent = INVALID_URL_MESSAGE;
          return;
        }
        window.open(peerUrl.href, '_blank', 'noopener,noreferrer');
        if (status) status.textContent = 'Client: Peer-Seite geöffnet; bitte selbst prüfen und anschließend bestätigen';
      });
    });

    document.querySelectorAll('[data-peer-browser-check]').forEach((button) => {
      button.addEventListener('click', async () => {
        const status = findStatus(button.dataset.peerBrowserCheck);
        if (!status) return;
        const peerUrl = safePeerUrl(button.dataset.peerUrl, window.URL);
        if (!peerUrl) {
          status.textContent = INVALID_URL_MESSAGE;
          return;
        }
        status.textContent = 'Client: Prüfung läuft…';
        const probeUrl = new window.URL('auth/login', peerUrl.href.endsWith('/') ? peerUrl.href : peerUrl.href + '/');
        const controller = new window.AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 5000);
        try {
          await window.fetch(probeUrl.href, {
            method: 'GET', mode: 'no-cors', credentials: 'omit', cache: 'no-store', signal: controller.signal,
          });
          status.textContent = UNVERIFIED_MESSAGE;
        } catch (_) {
          status.textContent = UNAVAILABLE_MESSAGE;
        } finally {
          window.clearTimeout(timeout);
        }
      });
    });

    document.querySelectorAll('[data-peer-manual-confirm]').forEach((button) => {
      button.addEventListener('click', async () => {
        const status = findStatus(button.dataset.peerManualConfirm);
        if (!status) return;
        const reachable = button.dataset.peerManualOutcome === 'reachable';
        const question = reachable
          ? 'Hast du die Peer-Seite selbst geöffnet und die Login-Seite gesehen?'
          : 'Hast du selbst geprüft, dass die Peer-Seite nicht erreichbar war?';
        if (!window.confirm(question)) return;
        status.textContent = 'Client: manuelle Bestätigung wird gespeichert…';
        try {
          const response = await window.fetch(button.dataset.peerConfirmUrl, {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
            body: new window.URLSearchParams({
              _csrf_token: button.dataset.peerConfirmCsrf,
              outcome: reachable ? 'reachable' : 'unreachable',
            }).toString(),
          });
          const result = await response.json();
          if (!response.ok || result.status !== 'recorded' || result.technical_verification !== false) {
            throw new Error('confirmation was not accepted');
          }
          const outcome = result.outcome === 'reachable' ? 'Seite geöffnet' : 'nicht erreichbar';
          const timestamp = new window.Date(Number(result.confirmed_at) * 1000).toLocaleString();
          status.textContent = 'Client: manuell bestätigt — ' + outcome + ' (' + timestamp + ', ' + result.actor + '), nicht technisch verifiziert';
        } catch (_) {
          status.textContent = 'Client: Bestätigung konnte nicht gespeichert werden; kein technischer Erreichbarkeitsnachweis';
        }
      });
    });
  }

  return {install, safePeerUrl};
});
