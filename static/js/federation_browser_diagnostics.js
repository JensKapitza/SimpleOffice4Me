(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SimpleOfficeFederationBrowserDiagnostics = api;
  if (root.document && typeof module !== "object") {
    const initialize = () => api.install(root.document, root);
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", initialize, {once: true});
    else initialize();
  }
})(typeof globalThis === "object" ? globalThis : this, function (root) {
  "use strict";

  const tx = (german, english) => root.document?.documentElement?.lang === "en" ? english : german;

  const INVALID_URL_MESSAGE = tx("Client: nicht auswertbar (ungültige Peer-Adresse); kein Verbindungsnachweis", "Client: cannot assess (invalid peer address); no connection evidence");
  const UNVERIFIED_MESSAGE = tx("Client: Anfrage ausgelöst, Antwort nicht verifizierbar (CSP, CORS, Mixed Content, TLS oder Browser-Schutz); kein Erreichbarkeitsnachweis", "Client: request sent, response cannot be verified (CSP, CORS, mixed content, TLS, or browser protection); no reachability evidence");
  const UNAVAILABLE_MESSAGE = tx("Client: nicht auswertbar (Netzwerk, Timeout, CSP, CORS, Mixed Content, TLS oder Browser-Schutz); Peer nicht zwingend offline", "Client: cannot assess (network, timeout, CSP, CORS, mixed content, TLS, or browser protection); peer is not necessarily offline");

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
        if (status) status.textContent = tx('Client: Peer-Seite geöffnet; bitte selbst prüfen und anschließend bestätigen', 'Client: peer page opened; review it yourself and confirm afterward');
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
        status.textContent = tx('Client: Prüfung läuft…', 'Client: checking…');
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
          ? tx('Hast du die Peer-Seite selbst geöffnet und die Login-Seite gesehen?', 'Did you open the peer page yourself and see the login page?')
          : tx('Hast du selbst geprüft, dass die Peer-Seite nicht erreichbar war?', 'Did you verify yourself that the peer page was unreachable?');
        if (!window.confirm(question)) return;
        status.textContent = tx('Client: manuelle Bestätigung wird gespeichert…', 'Client: saving manual confirmation…');
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
          const outcome = result.outcome === 'reachable' ? tx('Seite geöffnet', 'page opened') : tx('nicht erreichbar', 'unreachable');
          const timestamp = new window.Date(Number(result.confirmed_at) * 1000).toLocaleString();
          status.textContent = tx('Client: manuell bestätigt — ', 'Client: manually confirmed — ') + outcome + ' (' + timestamp + ', ' + result.actor + '), ' + tx('nicht technisch verifiziert', 'not technically verified');
        } catch (_) {
          status.textContent = tx('Client: Bestätigung konnte nicht gespeichert werden; kein technischer Erreichbarkeitsnachweis', 'Client: could not save confirmation; no technical reachability evidence');
        }
      });
    });
  }

  return {install, safePeerUrl};
});
