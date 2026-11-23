(function () {
  "use strict";

  const IS_ENGLISH = typeof document !== "undefined" && document.documentElement?.lang === "en";
  const tx = (german, english) => IS_ENGLISH ? english : german;
  const STATUS = {open: tx("Offen", "Open"), review: tx("In Prüfung", "In review"), blocked: tx("Blockiert", "Blocked"), missing_evidence: tx("Nachweis fehlt", "Evidence missing"), ready: tx("Bereit zum Merge", "Ready to merge"), done: tx("Erledigt", "Done")};
  const TYPES = {pr: "Pull Request", issue: "Issue", ci: "CI / Test", security: "Security", review: "Review"};
  const TESTS = {success: tx("Erfolgreich", "Passed"), failed: tx("Fehlgeschlagen", "Failed"), running: tx("Läuft", "Running"), unknown: tx("Ungeprüft", "Not verified"), cancelled: tx("Abgebrochen", "Cancelled")};
  const SOURCE_STATES = {ok: tx("Abgerufen", "Fetched"), partial: tx("Teilweise", "Partial"), error: tx("Fehler", "Error"), unavailable: tx("Nicht verfügbar", "Unavailable")};
  const STORAGE_KEY = "simpleoffice-repository-checklist-v1";

  function safeRepositoryUrl(value) {
    try {
      const url = new URL(String(value || ""));
      const path = url.pathname.toLowerCase();
      return url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password
        && (path === "/jenskapitza/simpleoffice4me" || path.startsWith("/jenskapitza/simpleoffice4me/")) ? url.href : null;
    } catch (_error) { return null; }
  }

  function filterItems(items, filters) {
    const query = String(filters.q || "").trim().toLocaleLowerCase();
    return items.filter((item) => {
      if (["priority", "area", "type", "status"].some((key) => filters[key] && item[key] !== filters[key])) return false;
      if (filters.platform && (filters.platform === "unknown" ? (item.platforms || []).length > 0 : !(item.platforms || []).includes(filters.platform))) return false;
      const searchable = [item.title, item.number, item.id, item.area, item.cause, ...(item.blockers || [])].join(" ").toLocaleLowerCase();
      return !query || searchable.includes(query);
    }).sort((a, b) => String(a.priority || "P3").localeCompare(String(b.priority || "P3"))
      || String(b.updated_at || "").localeCompare(String(a.updated_at || "")) || String(a.id).localeCompare(String(b.id)));
  }

  function md(value) { return String(value || "").replace(/[\\`*_{}\[\]<>#|!]/g, "\\$&"); }
  function lines(values, fallback) { return Array.isArray(values) && values.length ? values.map((value) => `- ${md(value)}`).join("\n") : md(fallback); }
  function evidenceMarkdown(values) {
    return (values || []).map((entry) => {
      const url = safeRepositoryUrl(entry.url);
      return url ? `- [${md(entry.label || tx("Quelle", "Source"))}](${url.replace(/\)/g, "%29")})` : "";
    }).filter(Boolean).join("\n") || tx("Keine belegte Quelle hinterlegt.", "No verified source recorded.");
  }

  function itemMarkdown(item, fetchedAt) {
    return `## ${md(item.title)}\n\n`
      + `${tx("Typ", "Type")}: ${md(TYPES[item.type] || item.type)} · ${tx("Priorität", "Priority")}: ${md(item.priority)} · ${tx("Status", "Status")}: ${md(STATUS[item.status] || item.status)}\n\n`
      + `${tx("Bereich", "Area")}: ${md(item.area)}\n\n${tx("Plattform / Prüfumfang", "Platform / test scope")}: ${md((item.platforms || []).join(", ") || tx("Nicht zugeordnet / ungeprüft", "Unassigned / not verified"))}\n${md(item.platform_assessment || tx("Betroffenheit ungeprüft.", "Impact not verified."))}\n\n${tx("Datenstand", "Data as of")}: ${md(fetchedAt || tx("unbekannt", "unknown"))}\n\n`
      + `### ${tx("Ursache / offene Frage", "Cause / open question")}\n\n${md(item.cause || tx("Ursache ungeprüft.", "Cause not verified."))}\n\n`
      + `### ${tx("Bekannte Blocker", "Known blockers")}\n\n${lines(item.blockers, tx("Keine bestätigten Blocker erfasst; das ist kein Freigabenachweis.", "No confirmed blockers recorded; this is not evidence for approval."))}\n\n`
      + `### ${tx("Nächste Prüfschritte", "Next review steps")}\n\n${lines(item.next_steps, tx("Prüfschritte noch festlegen.", "Review steps still need to be defined."))}\n\n`
      + `### ${tx("Teststatus", "Test status")}\n\n${md(item.test_status?.summary || tx("Ungeprüft", "Not verified"))}\n\n`
      + `### ${tx("Benötigte Dokumentation", "Documentation needed")}\n\n${lines(item.documentation, tx("Dokumentationsbedarf prüfen.", "Review documentation needs."))}\n\n`
      + `### ${tx("Merge grundsätzlich vorbereitbar?", "Is this ready for merge preparation?")}\n\n${md(item.merge_preparation?.summary || tx("Ungeprüft; keine Merge-Freigabe.", "Not verified; no merge approval."))}\n\n`
      + `### ${tx("Einschätzung / Unsicherheit", "Assessment / uncertainty")}\n\n${md(item.assessment || tx("Noch nicht bewertet.", "Not assessed yet."))}\n\n`
      + `### ${tx("Quellen", "Sources")}\n\n${evidenceMarkdown([{label: tx("GitHub-Eintrag", "GitHub entry"), url: item.url}, ...(item.evidence || [])])}\n`;
  }

  function gapMarkdown(gap) {
    return `## ${md(gap.title)}\n\n${tx("Bereich", "Area")}: ${md(gap.area || tx("Noch zuzuordnen", "Unassigned"))}\n\n`
      + `${tx("Plattform / Prüfumfang", "Platform / test scope")}: ${md((gap.platforms || []).join(", ") || tx("Nicht zugeordnet / ungeprüft", "Unassigned / not verified"))}\n\n`
      + `### ${tx("Offene Frage", "Open question")}\n\n${md(gap.cause)}\n\n### ${tx("Nächste Prüfschritte", "Next review steps")}\n\n${lines(gap.next_steps, tx("Noch festzulegen.", "To be defined."))}\n\n`
      + `### ${tx("Teststatus", "Test status")}\n\n${md(gap.tests || tx("Noch ungeprüft.", "Not verified yet."))}\n\n`
      + `### ${tx("Benötigte Dokumentation", "Documentation needed")}\n\n${lines(gap.documentation, tx("Noch festzulegen.", "To be defined."))}\n\n`
      + `### ${tx("Einschätzung", "Assessment")}\n\n${md(gap.assessment || tx("Lokaler Entwurf, noch nicht geprüft und nicht als Issue veröffentlicht.", "Local draft; not reviewed or published as an issue."))}\n\n`
      + `### ${tx("Quellen / vorhandene Tracker", "Sources / existing trackers")}\n\n${evidenceMarkdown(gap.evidence)}\n`;
  }

  function checklistEntries(snapshot) {
    return [...(snapshot?.checklist || []), ...Object.values(snapshot?.platform_checklists || {}).flat()];
  }

  function platformChecklistMarkdown(snapshot, checks) {
    let content = `\n## ${tx("Plattform-Abnahme", "Platform acceptance")}\n\n${tx("Persönliche Häkchen sind keine bestätigte Abnahme. Nachweise: Commit, Gerät/OS, Ablauf, Soll/Ist und Artefakte.", "Personal checkmarks do not confirm acceptance. Evidence: commit, device/OS, procedure, expected/actual results, and artifacts.")}\n`;
    Object.entries(snapshot?.platform_checklists || {}).forEach(([platform, rows]) => {
      content += `\n### ${md(platform)}\n\n`;
      rows.forEach((entry) => { content += `- [${checks[entry.id] ? "x" : " "}] ${md(entry.title)} — ${md(entry.detail)}\n  ${tx("Nachweis: offen; persönliche Arbeitsmarkierung.", "Evidence: open; personal work marker.")}\n`; });
    });
    return content;
  }

  function readChecks(storage) {
    const value = JSON.parse(storage.getItem(STORAGE_KEY) || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(tx("Ungültige Checkliste", "Invalid checklist"));
    return Object.fromEntries(Object.entries(value).filter(([key, checked]) => /^[a-zA-Z0-9_-]{1,80}$/.test(key) && checked === true));
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {filterItems, safeRepositoryUrl, itemMarkdown, gapMarkdown, readChecks, checklistEntries, platformChecklistMarkdown};
  }
  if (typeof document === "undefined") return;
  const root = document.querySelector("[data-repository-workspace]");
  if (!root) return;

  const $ = (id) => document.getElementById(`rw-${id}`);
  const state = {snapshot: null, auth: null, epoch: 0, authEpoch: 0, pending: false, loading: false, mode: "board", view: "overview", checks: {}, bookmarks: new Map(), drafts: [], selected: null, controllers: new Set(), pollTimer: null, expiryTimer: null, cooldownTimer: null};

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function button(text, handler, className = "btn btn-outline-secondary btn-sm") {
    const node = element("button", className, text); node.type = "button"; node.addEventListener("click", handler); return node;
  }
  function badge(text, className = "") { return element("span", `rw-badge ${className}`, text); }
  function message(text) { $("message").textContent = text; }
  function dateText(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.valueOf()) ? tx("unbekannt", "unknown") : date.toLocaleString(IS_ENGLISH ? "en-GB" : "de-DE", {dateStyle: "medium", timeStyle: "short"});
  }
  function machineDateTime(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.valueOf()) ? "" : date.toISOString();
  }
  function empty(text) { return element("p", "rw-empty mb-0", text); }
  function sourceLink(label, value) {
    const url = safeRepositoryUrl(value);
    if (!url) return null;
    const link = element("a", "", label); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
  }
  function evidenceLinks(values) {
    const box = element("div", "rw-evidence");
    const seen = new Set();
    (values || []).forEach((entry) => {
      const link = sourceLink(entry.label || tx("GitHub-Quelle", "GitHub source"), entry.url);
      if (link && !seen.has(link.href)) { seen.add(link.href); box.append(link); }
    });
    if (!box.childNodes.length) box.append(empty(tx("Keine belegte Quelle hinterlegt.", "No verified source recorded.")));
    return box;
  }

  async function request(path, method = "GET") {
    const controller = new AbortController();
    state.controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 100000);
    try {
      const response = await fetch(`${root.dataset.apiBase}${path}`, {
        method, credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: {Accept: "application/json", ...(method === "POST" ? {"X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""} : {})},
      });
      let data;
      try { data = await response.json(); } catch (_error) { throw new Error(tx("Der Server lieferte keine lesbare Antwort. Bitte die Seite neu laden.", "The server returned an unreadable response. Reload the page.")); }
      if (!response.ok) {
        const error = new Error(response.status === 401 ? tx("GitHub-Anmeldung ist nicht mehr gültig.", "GitHub sign-in is no longer valid.")
          : response.status === 403 ? tx("Anfrage nicht freigegeben. Bitte die Seite neu laden und erneut versuchen.", "Request was not authorized. Reload the page and try again.")
          : response.status === 429 ? tx("Abruflimit erreicht. Bitte später erneut aktualisieren.", "Rate limit reached. Try refreshing later.")
            : tx("Abruf nicht verfügbar. Bitte später erneut versuchen.", "Fetch unavailable. Please try again later."));
        error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(tx("Abruf abgebrochen oder Zeitlimit erreicht.", "Fetch was cancelled or timed out."));
      if (error instanceof TypeError) throw new Error(tx("Verbindung zum Server fehlgeschlagen. Bitte die Verbindung prüfen.", "Could not connect to the server. Check your connection."));
      throw error;
    } finally { clearTimeout(timeout); state.controllers.delete(controller); }
  }

  function invalidate() {
    state.epoch += 1;
    state.controllers.forEach((controller) => controller.abort());
    state.controllers.clear();
    state.snapshot = null; state.selected = null; state.auth = null; state.bookmarks.clear(); state.drafts = [];
    state.loading = false;
    clearTimeout(state.cooldownTimer);
    clearTimeout(state.expiryTimer);
    if ($("detail").open) $("detail").close();
    $("detail-body").replaceChildren(); $("detail-title").textContent = "";
    $("filters").reset();
    $("draft-form").reset();
    $("auth-title").textContent = tx("Kontostatus wird geprüft", "Checking account status");
    $("auth-expiry").textContent = ""; $("auth-reason").textContent = "";
    $("auth-description").textContent = tx("Kontodaten werden erst nach erneuter Prüfung der Sitzung angezeigt.", "Account data will be shown only after the session is checked again.");
    $("login").disabled = true; $("logout").hidden = true;
    $("updated").textContent = tx("Daten werden für den aktuellen Kontokontext neu geladen.", "Reloading data for the current account context.");
    renderAll();
  }

  function applyAuth(auth) {
    if (!auth || typeof auth.context_id !== "string") throw new Error(tx("Kontostatus konnte nicht geprüft werden.", "Could not verify account status."));
    const changed = state.auth && (state.auth.context_id !== auth.context_id || Boolean(state.auth.authenticated) !== Boolean(auth.authenticated));
    if (changed) invalidate();
    state.auth = auth;
    $("auth-title").textContent = auth.authenticated ? `${tx("Kontoansicht", "Account view")} · ${auth.login || "GitHub"}` : tx("Öffentliche Ansicht", "Public view");
    $("auth-description").textContent = auth.authenticated
      ? tx("Zusätzliche Daten bleiben an diese Sitzung gebunden. Sichtbar ist nur, was dein Konto und die GitHub App lesen dürfen.", "Additional data stays bound to this session. You can see only what your account and the GitHub App are allowed to read.")
      : tx("Öffentliche Repository-Daten sind ohne Konto lesbar. Ein GitHub-Konto kann zusätzlich freigegebene Prüf- und Security-Daten sichtbar machen.", "Public repository data is available without an account. A GitHub account can also show authorized review and security data.");
    $("login").hidden = Boolean(auth.authenticated);
    $("login").disabled = !auth.can_authenticate || state.pending;
    $("logout").hidden = !auth.authenticated;
    $("auth-reason").textContent = auth.reason || "";
    $("auth-reason").hidden = !auth.reason;
    $("auth-expiry").textContent = auth.authenticated && auth.expires_at ? `${tx("Anmeldung bis", "Signed in until")} ${dateText(auth.expires_at)}` : "";
    clearTimeout(state.expiryTimer);
    const ttl = new Date(auth.expires_at || "").getTime() - Date.now();
    if (auth.authenticated && Number.isFinite(ttl)) {
      state.expiryTimer = setTimeout(() => {
        invalidate();
        message(tx("Anmeldung abgelaufen. Der Kontostatus wird neu geprüft.", "Sign-in expired. Checking account status again."));
        refreshStatus(true);
      }, Math.max(1000, Math.min(ttl + 500, 2147483647)));
    }
    return changed;
  }

  async function refreshStatus(loadWhenChanged = false) {
    const epoch = state.epoch;
    try {
      const auth = await request("/status");
      if (epoch !== state.epoch) return;
      const changed = applyAuth(auth);
      if (loadWhenChanged && (changed || !state.snapshot)) await loadSnapshot();
      return auth;
    } catch (error) {
      if (epoch !== state.epoch) return;
      if (state.auth?.authenticated) invalidate();
      $("login").disabled = true;
      message(error.message);
    }
  }

  function refreshButton() {
    const remaining = new Date(state.snapshot?.next_refresh_at || "").getTime() - Date.now();
    $("refresh").disabled = state.loading || remaining > 0;
    $("refresh").title = remaining > 0 ? `${tx("Nächster Abruf ab", "Next fetch at")} ${dateText(state.snapshot.next_refresh_at)}` : tx("GitHub-Daten erneut lesen", "Refresh GitHub data");
    $("refresh").setAttribute("aria-describedby", "rw-refresh-hint");
    $("refresh-hint").textContent = remaining > 0 ? `${tx("GitHub-Abruflimit: nächste Aktualisierung ab", "GitHub rate limit: next refresh at")} ${dateText(state.snapshot.next_refresh_at)}.` : "";
    clearTimeout(state.cooldownTimer);
    if (remaining > 0) state.cooldownTimer = setTimeout(refreshButton, Math.min(remaining + 100, 2147483647));
  }

  async function loadSnapshot(force = false) {
    if (state.loading) return;
    const epoch = state.epoch; state.loading = true; refreshButton(); message(tx("Repository-Daten werden gelesen …", "Loading repository data …"));
    try {
      const data = await request(force ? "/refresh" : "/snapshot", force ? "POST" : "GET");
      if (epoch !== state.epoch) return;
      if (!Array.isArray(data.items) || !Array.isArray(data.sources)) throw new Error(tx("Die Repository-Daten sind unvollständig. Bitte erneut abrufen.", "Repository data is incomplete. Please refresh."));
      applyAuth(data.auth);
      if ((data.mode === "account") !== Boolean(state.auth.authenticated)) { message(tx("Kontokontext hat sich geändert. Bitte erneut abrufen.", "Account context changed. Please refresh.")); return; }
      state.snapshot = data;
      renderAll();
      $("updated").textContent = `${tx("Stand", "As of")}: ${dateText(data.fetched_at)}${data.cached ? ` · ${tx("Zwischengespeicherter Abruf", "Cached fetch")}` : ""} · ${data.mode === "account" ? tx("Kontoansicht", "Account view") : tx("Öffentlich", "Public")}`;
      $("updated").title = data.fetched_at || "";
      message(tx("Abruf abgeschlossen.", "Fetch complete."));
    } catch (error) {
      if (epoch === state.epoch && error.status === 401) {
        const wasAccount = state.auth?.authenticated || state.snapshot?.mode === "account";
        invalidate(); message(tx("GitHub-Anmeldung nicht mehr gültig. Kontodaten und lokale Kontonotizen wurden entfernt.", "GitHub sign-in is no longer valid. Account data and local account notes were removed."));
        if (wasAccount) await refreshStatus(true);
      } else if (epoch === state.epoch) {
        message(`${error.message}${state.snapshot ? ` ${tx("Angezeigte Daten behalten ihren bisherigen Stand.", "Displayed data remains at its previous state.")}` : ""}`);
      }
    } finally {
      if (epoch === state.epoch || !state.loading) { state.loading = false; refreshButton(); }
    }
  }

  function stopPoll() {
    state.authEpoch += 1; clearTimeout(state.pollTimer); state.pending = false;
    $("device").hidden = true; $("device-code").value = ""; $("device-status").textContent = "";
    $("login").disabled = !state.auth?.can_authenticate;
  }
  async function logout() {
    stopPoll(); invalidate(); clearTimeout(state.expiryTimer);
    $("login").disabled = true; $("logout").disabled = true;
    const epoch = state.epoch;
    try {
      await request("/auth/logout", "POST");
      if (epoch !== state.epoch) return;
      const auth = await refreshStatus(); if (auth) await loadSnapshot();
      message(tx("GitHub-Sitzung beendet. Lokale Kontonotizen wurden entfernt.", "GitHub session ended. Local account notes were removed."));
    } catch (error) { if (epoch === state.epoch) message(`${tx("Abmeldung nicht bestätigt.", "Sign-out was not confirmed.")} ${error.message}`); }
    finally { $("logout").disabled = false; }
  }
  async function startAuth() {
    if (state.pending || !state.auth?.can_authenticate) return;
    stopPoll(); state.pending = true; $("login").disabled = true;
    const epoch = state.authEpoch;
    try {
      const result = await request("/auth/start", "POST");
      if (epoch !== state.authEpoch) return;
      if (result.status !== "pending" || result.verification_uri !== "https://github.com/login/device" || !result.user_code) throw new Error(tx("GitHub-Anmeldung konnte nicht vorbereitet werden.", "Could not prepare GitHub sign-in."));
      $("device").hidden = false; $("device-code").value = result.user_code;
      $("device-status").textContent = `${tx("Warte auf Bestätigung bei GitHub. Code gültig bis", "Waiting for confirmation on GitHub. Code expires at")} ${dateText(result.expires_at)}.`;
      $("device-code").focus();
      schedulePoll(result.interval, epoch, new Date(result.expires_at).getTime());
    } catch (error) { if (epoch === state.authEpoch) { stopPoll(); message(error.message); } }
  }
  function schedulePoll(interval, epoch, expiresAt) {
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) { stopPoll(); message(tx("Anmeldecode abgelaufen. Du kannst eine neue Anmeldung starten.", "Sign-in code expired. You can start a new sign-in.")); return; }
    const delay = Math.min(expiresAt - Date.now(), Math.max(5, Number(interval) || 5) * 1000);
    state.pollTimer = setTimeout(() => pollAuth(epoch, expiresAt), delay);
  }
  async function pollAuth(epoch, expiresAt) {
    if (epoch !== state.authEpoch || !state.pending) return;
    if (expiresAt <= Date.now()) { stopPoll(); message(tx("Anmeldecode abgelaufen. Bitte bei Bedarf erneut anmelden.", "Sign-in code expired. Sign in again if needed.")); return; }
    try {
      const result = await request("/auth/poll", "POST");
      if (epoch !== state.authEpoch) return;
      if (["pending", "slow_down"].includes(result.status)) {
        $("device-status").textContent = result.status === "slow_down" ? tx("GitHub begrenzt die Abfragen. Die nächste Prüfung erfolgt später.", "GitHub is rate limiting requests. The next check will happen later.") : tx("Warte auf Bestätigung bei GitHub …", "Waiting for confirmation on GitHub …");
        schedulePoll(result.interval, epoch, expiresAt);
      } else if (result.status === "authenticated") {
        stopPoll(); invalidate(); const auth = await refreshStatus(); if (auth) await loadSnapshot();
        message(tx("GitHub-Anmeldung bestätigt. Berechtigte Daten wurden neu angefordert.", "GitHub sign-in confirmed. Authorized data was requested again."));
      } else {
        stopPoll(); message(result.status === "denied" ? tx("GitHub-Anmeldung wurde nicht freigegeben.", "GitHub sign-in was denied.") : result.status === "expired" ? tx("Anmeldecode abgelaufen. Bitte bei Bedarf erneut anmelden.", "Sign-in code expired. Sign in again if needed.") : tx("GitHub-Anmeldung fehlgeschlagen. Bitte erneut versuchen.", "GitHub sign-in failed. Please try again."));
      }
    } catch (error) { if (epoch === state.authEpoch) { stopPoll(); message(error.message); } }
  }

  function currentFilters() { return {q: $("search").value, priority: $("priority").value, area: $("area").value, type: $("type").value, status: $("status").value, platform: $("platform").value}; }
  function fillAreas() {
    const select = $("area"), value = select.value;
    select.replaceChildren(new Option(tx("Alle Bereiche", "All areas"), ""));
    [...new Set((state.snapshot?.items || []).map((item) => item.area).filter(Boolean))].sort().forEach((area) => select.add(new Option(area, area)));
    if ([...select.options].some((option) => option.value === value)) select.value = value;
  }
  function itemBadges(item) {
    const box = element("div", "rw-actions");
    box.append(badge(item.priority || "P3", `rw-priority-${String(item.priority || "P3").toLowerCase()}`), badge(STATUS[item.status] || tx("Ungeprüft", "Not verified")), badge(item.area || tx("Zuordnung offen", "Area unassigned")));
    return box;
  }
  function itemCard(item) {
    const card = button("", () => openDetail(item), "rw-entry");
    const top = element("span", "rw-entry-top");
    top.append(element("span", "rw-entry-meta", `${TYPES[item.type] || tx("Eintrag", "Entry")}${item.number ? ` #${item.number}` : ""}`), badge(item.priority || "P3", `rw-priority-${String(item.priority || "P3").toLowerCase()}`));
    card.append(top, element("span", "rw-entry-title", item.title));
    const bottom = element("span", "rw-entry-bottom"); bottom.append(badge(item.area || tx("Zuordnung offen", "Area unassigned"))); card.append(bottom);
    card.append(element("span", "rw-entry-test", `${tx("Prüfumfang", "Test scope")}: ${(item.platforms || []).join(" · ") || tx("Nicht zugeordnet / ungeprüft", "Unassigned / not verified")}`));
    card.append(element("span", "rw-entry-test", `${tx("Tests", "Tests")}: ${item.test_status?.summary || tx("Ungeprüft", "Not verified")}`));
    return card;
  }
  function renderOverview() {
    const all = state.snapshot?.items || [], items = filterItems(all, currentFilters());
    $("results").textContent = state.snapshot ? (IS_ENGLISH ? `${items.length} of ${all.length} recorded entries` : `${items.length} von ${all.length} erfassten Einträgen`) : tx("Noch keine bestätigten Repository-Daten.", "No confirmed repository data yet.");
    $("board").replaceChildren();
    Object.entries(STATUS).forEach(([status, title]) => {
      const selected = items.filter((item) => item.status === status), column = element("section", "rw-column");
      const heading = element("h3", "rw-column-heading", title); heading.append(element("span", "rw-column-count", selected.length));
      const body = element("div", "rw-column-body");
      selected.forEach((item) => body.append(itemCard(item)));
      if (!selected.length) body.append(empty(status === "ready" ? tx("Keine belegte Merge-Bereitschaft.", "No verified merge readiness.") : tx("Keine Einträge in dieser Auswahl.", "No entries in this selection.")));
      column.append(heading, body); $("board").append(column);
    });
    const table = element("table", "table rw-table");
    const caption = element("caption", "visually-hidden", tx("Gefilterte Repository-Einträge nach Priorität", "Filtered repository entries by priority"));
    const head = element("thead"), row = element("tr");
    [tx("Priorität", "Priority"), tx("Eintrag", "Entry"), tx("Bereich", "Area"), tx("Status", "Status"), tx("Plattform / Prüfumfang", "Platform / test scope"), tx("Teststatus", "Test status")].forEach((label) => { const cell = element("th", "", label); cell.scope = "col"; row.append(cell); }); head.append(row);
    const body = element("tbody");
    items.forEach((item) => {
      const row = element("tr"), priority = element("td"), title = element("td");
      priority.append(badge(item.priority, `rw-priority-${String(item.priority).toLowerCase()}`));
      title.append(button(`${item.number ? `#${item.number} · ` : ""}${item.title}`, () => openDetail(item), "rw-row-button"), element("div", "small text-secondary", TYPES[item.type] || tx("Eintrag", "Entry")));
      row.append(priority, title, element("td", "", item.area), element("td", "", STATUS[item.status]), element("td", "", (item.platforms || []).join(" · ") || tx("Ungeprüft", "Not verified")), element("td", "", item.test_status?.summary || tx("Ungeprüft", "Not verified"))); body.append(row);
    });
    table.append(caption, head, body); $("list").replaceChildren(items.length ? table : empty(tx("Keine Einträge in dieser Auswahl.", "No entries in this selection.")));
    $("board").hidden = state.mode !== "board"; $("list").hidden = state.mode !== "list";
  }
  function renderStats() {
    const items = state.snapshot?.items || [], box = $("stats"); box.replaceChildren(); box.hidden = !state.snapshot;
    const stats = [[items.filter((item) => item.type === "pr" && item.status !== "done").length, tx("Offene PRs erfasst", "Open PRs recorded")], [items.filter((item) => item.type === "issue" && item.status !== "done").length, tx("Offene Issues erfasst", "Open issues recorded")], [items.filter((item) => item.status === "blocked").length, tx("Als blockiert eingeordnet", "Marked as blocked")], [items.filter((item) => ["security", "review"].includes(item.type) && item.status !== "done").length, tx("Security- / Review-Funde erfasst", "Security / review findings recorded")]];
    const securitySources = (state.snapshot?.sources || []).filter((source) => /security|scanning|dependabot/.test(source.id));
    const securityIncomplete = !securitySources.length || securitySources.some((source) => source.state !== "ok");
    stats.forEach(([count, label], index) => {
      const card = element("div", "rw-stat");
      card.append(element("strong", "", index === 3 && !count && securityIncomplete ? tx("Ungeprüft", "Not verified") : count), element("span", "", label));
      if (index === 3 && securityIncomplete) card.append(element("div", "small text-secondary mt-1", tx("Security-Abdeckung unvollständig", "Security coverage incomplete")));
      box.append(card);
    });
    const partial = (state.snapshot?.sources || []).filter((source) => source.state !== "ok");
    $("warning").hidden = !partial.length;
    $("warning").textContent = partial.length ? (IS_ENGLISH ? `${partial.length} source(s) only partially checked or unchecked. Counts include recorded entries only; see “Sources & coverage” for details.` : `${partial.length} Quelle(n) nur teilweise oder nicht geprüft. Zähler zeigen ausschließlich erfasste Einträge; Details unter „Quellen & Abdeckung“.`) : "";
  }
  function detailSection(title, value, fallback) {
    const section = element("section", "rw-detail-section"); section.append(element("h3", "", title));
    if (Array.isArray(value) && value.length) { const list = element("ul", "mb-0"); value.forEach((entry) => list.append(element("li", "", entry))); section.append(list); }
    else section.append(element("p", "mb-0", typeof value === "string" && value ? value : fallback));
    return section;
  }
  function openDetail(item) {
    state.selected = item; $("detail-title").textContent = item.title;
    const body = $("detail-body"); body.replaceChildren(itemBadges(item));
    body.append(detailSection(tx("Ursache / offene Frage", "Cause / open question"), item.cause, tx("Ursache ungeprüft.", "Cause not verified.")), detailSection(tx("Bekannte Blocker", "Known blockers"), item.blockers, tx("Keine bestätigten Blocker erfasst. Das ist kein Freigabenachweis.", "No confirmed blockers recorded. This is not evidence for approval.")), detailSection(tx("Nächste Prüfschritte", "Next review steps"), item.next_steps, tx("Prüfschritte noch festlegen.", "Review steps still need to be defined.")), detailSection(tx("Teststatus", "Test status"), `${TESTS[item.test_status?.state] || tx("Ungeprüft", "Not verified")} · ${item.test_status?.summary || tx("Kein belastbarer Testnachweis vorhanden.", "No reliable test evidence available.")}`, tx("Ungeprüft", "Not verified")), detailSection(tx("Benötigte Dokumentation", "Documentation needed"), item.documentation, tx("Dokumentationsbedarf prüfen.", "Review documentation needs.")), detailSection(tx("Einschätzung / Unsicherheit", "Assessment / uncertainty"), item.assessment, tx("Noch nicht bewertet.", "Not assessed yet.")));
    body.append(detailSection(tx("Plattform / Prüfumfang", "Platform / test scope"), (item.platforms || []).join(" · ") || tx("Nicht zugeordnet / ungeprüft", "Unassigned / not verified"), tx("Ungeprüft", "Not verified")), detailSection(tx("Plattform-Einordnung", "Platform assessment"), item.platform_assessment, tx("Betroffenheit ungeprüft.", "Impact not verified.")), detailSection(tx("Merge grundsätzlich vorbereitbar?", "Ready for merge preparation?"), item.merge_preparation?.summary, tx("Ungeprüft; keine Merge-Freigabe.", "Not verified; no merge approval.")));
    if (item.head_sha) body.append(detailSection(tx("Beobachteter Commit", "Observed commit"), item.head_sha, tx("Unbekannt", "Unknown")));
    const sources = element("section", "rw-detail-section"); sources.append(element("h3", "", tx("Quellen", "Sources")), evidenceLinks([{label: tx("Eintrag bei GitHub", "GitHub entry"), url: item.url}, ...(item.evidence || [])])); body.append(sources);
    body.append(element("p", "small text-secondary mt-3", `${tx("Abrufstand", "Fetched")}: ${dateText(state.snapshot?.fetched_at)} · ${tx("Letzte Änderung", "Last updated")}: ${dateText(item.updated_at)}`));
    $("bookmark").textContent = state.bookmarks.has(item.id) ? tx("In Arbeitsliste vorgemerkt", "Saved to worklist") : tx("Für Arbeitsliste vormerken", "Save to worklist");
    $("bookmark").disabled = state.bookmarks.has(item.id);
    if (!$("detail").open) $("detail").showModal();
  }

  function saveChecks() {
    const allowed = new Set(checklistEntries(state.snapshot).map((entry) => entry.id));
    const safe = Object.fromEntries(Object.entries(state.checks).filter(([key, checked]) => allowed.has(key) && checked === true));
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(safe)); }
    catch (_error) { message(tx("Häkchen können in diesem Browser nicht gespeichert werden. Export bleibt verfügbar.", "Checkmarks cannot be saved in this browser. Export is still available.")); }
  }
  function appendCheckRows(box, entries, prefix) {
    entries.forEach((entry, index) => {
      const row = element("div", "rw-check"), input = element("input", ""); input.type = "checkbox"; input.id = `rw-${prefix}-${index}`; input.checked = Boolean(state.checks[entry.id]);
      input.addEventListener("change", () => { state.checks[entry.id] = input.checked; saveChecks(); });
      const label = element("label", ""); label.htmlFor = input.id; label.append(element("strong", "", entry.title), element("small", "", entry.detail)); row.append(input, label); box.append(row);
    });
  }
  function renderChecklist() {
    const box = $("checklist"); box.replaceChildren();
    appendCheckRows(box, state.snapshot?.checklist || [], "step");
    if (!box.childNodes.length) box.append(empty(tx("Die Arbeitsliste wird mit dem Repository-Abruf geladen.", "The worklist loads with repository data.")));
    const platforms = $("platform-checklists"); platforms.replaceChildren();
    Object.entries(state.snapshot?.platform_checklists || {}).forEach(([platform, entries]) => {
      const section = element("section", "card rw-platform-checklist"); section.append(element("h4", "h6", platform));
      appendCheckRows(section, entries, `platform-${platform.toLowerCase()}`); platforms.append(section);
    });
  }
  function renderBookmarks() {
    const box = $("bookmarks"); box.replaceChildren();
    state.bookmarks.forEach((entry, id) => {
      const card = element("article", "card rw-note-card"), title = element("h3", "", entry.item.title);
      const label = element("label", "form-label small", tx("Prüfnotiz · nur in diesem Tab", "Review note · this tab only")), note = element("textarea", "form-control mb-3");
      note.id = `rw-note-${box.childNodes.length}`; note.rows = 3; note.maxLength = 6000; note.value = entry.note; label.htmlFor = note.id; note.addEventListener("input", () => { entry.note = note.value; });
      const actions = element("div", "rw-actions"); actions.append(button(tx("Details", "Details"), () => openDetail(entry.item)), button(tx("Vormerkung entfernen", "Remove from worklist"), () => { state.bookmarks.delete(id); renderBookmarks(); }));
      card.append(title, itemBadges(entry.item), detailSection(tx("Nächste Prüfschritte", "Next review steps"), entry.item.next_steps, tx("Noch festzulegen.", "To be defined.")), label, note, actions); box.append(card);
    });
    if (!box.childNodes.length) box.append(empty(tx("Noch keine Einträge vorgemerkt.", "No entries saved yet.")));
  }
  function gapCard(gap, local = false) {
    const card = element("article", "card rw-note-card"); card.append(element("h3", "", gap.title), badge(gap.area || tx("Bereich offen", "Area unassigned")), detailSection(tx("Offene Frage", "Open question"), gap.cause, tx("Noch zu prüfen.", "To be reviewed.")), detailSection(tx("Nächste Prüfschritte", "Next review steps"), gap.next_steps, tx("Noch festzulegen.", "To be defined.")), detailSection(tx("Benötigte Dokumentation", "Documentation needed"), gap.documentation, tx("Noch festzulegen.", "To be defined.")));
    card.append(detailSection(tx("Plattform / Prüfumfang", "Platform / test scope"), (gap.platforms || []).join(" · ") || tx("Nicht zugeordnet / ungeprüft", "Unassigned / not verified"), tx("Ungeprüft", "Not verified")));
    if (gap.tests) card.append(detailSection(tx("Teststatus", "Test status"), gap.tests, tx("Ungeprüft", "Not verified")));
    card.append(element("p", "text-secondary mt-3", gap.assessment || tx("Lokaler Entwurf, ungeprüft und nicht als Issue veröffentlicht.", "Local draft; not reviewed or published as an issue.")), evidenceLinks(gap.evidence));
    const actions = element("div", "rw-actions mt-3"); actions.append(button(tx("Entwurf exportieren", "Export draft"), () => download("arbeitsauftrag.md", exportHeader() + gapMarkdown(gap))));
    if (local) actions.append(button(tx("Entwurf entfernen", "Remove draft"), () => { state.drafts = state.drafts.filter((item) => item !== gap); renderGaps(); }));
    card.append(actions); return card;
  }
  function renderGaps() {
    $("gap-list").replaceChildren(); $("local-drafts").replaceChildren();
    (state.snapshot?.documentation_gaps || []).forEach((gap) => $("gap-list").append(gapCard(gap)));
    if (!$("gap-list").childNodes.length) $("gap-list").append(empty(state.snapshot ? tx("Keine Dokumentationshinweise im erfassten Bestand. Weitere offene Punkte kannst du als lokalen Entwurf aufnehmen.", "No documentation gaps in the recorded data. You can add further open items as local drafts.") : tx("Noch keine Dokumentationshinweise geladen.", "No documentation notes loaded yet.")));
    state.drafts.forEach((gap) => $("local-drafts").append(gapCard(gap, true)));
  }
  function renderSources() {
    const box = $("source-list"); box.replaceChildren();
    (state.snapshot?.sources || []).forEach((source) => {
      const row = element("article", "rw-source"), title = element("div", "fw-semibold", source.label);
      title.append(element("span", "", dateText(source.checked_at)));
      const machineTime = machineDateTime(source.checked_at);
      if (machineTime) { const time = element("time", "visually-hidden", machineTime); time.dateTime = machineTime; title.append(time); }
      row.append(title, badge(SOURCE_STATES[source.state] || tx("Ungeprüft", "Not verified"), source.state === "ok" ? "rw-badge-readonly" : "rw-priority-p2"), element("p", "mb-0", source.message || tx("Keine weiteren Angaben.", "No additional information."))); box.append(row);
    });
    if (!box.childNodes.length) box.append(empty(tx("Noch kein Quellenabruf bestätigt.", "No source fetch confirmed yet.")));
  }
  function renderAll() { fillAreas(); renderStats(); renderOverview(); renderChecklist(); renderBookmarks(); renderGaps(); renderSources(); }

  function exportHeader() { return `# SimpleOffice4Me · ${tx("Arbeitszentrale", "Work hub")}\n\n${tx("Nur Planung und Dokumentation. Keine GitHub-Aktionen ausgeführt.", "Planning and documentation only. No GitHub actions were performed.")}\n\n${tx("Datenstand", "Data as of")}: ${md(state.snapshot?.fetched_at || tx("unbekannt", "unknown"))} · ${tx("Ansicht", "View")}: ${state.snapshot?.mode === "account" ? tx("kontogebunden; kann nicht öffentliche Informationen enthalten", "account; may contain non-public information") : tx("öffentlich", "public")}\n\n`; }
  function download(filename, content) {
    const url = URL.createObjectURL(new Blob([content], {type: "text/markdown;charset=utf-8"}));
    const link = element("a", ""); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    message(tx("Markdown-Export erstellt.", "Markdown export created."));
  }
  function exportWorklist() {
    let content = exportHeader() + `## ${tx("Wiederverwendbare Arbeitsliste", "Reusable worklist")}\n\n`;
    (state.snapshot?.checklist || []).forEach((entry) => { content += `- [${state.checks[entry.id] ? "x" : " "}] ${md(entry.title)} — ${md(entry.detail)}\n`; });
    content += platformChecklistMarkdown(state.snapshot, state.checks);
    state.bookmarks.forEach((entry) => { content += `\n${itemMarkdown(entry.item, state.snapshot?.fetched_at)}\n### ${tx("Lokale Prüfnotiz", "Local review note")}\n\n${md(entry.note || tx("Noch keine Notiz.", "No note yet."))}\n`; });
    download("simpleoffice-arbeitsliste.md", content);
  }
  function switchView(view) {
    state.view = view;
    root.querySelectorAll("[data-rw-view]").forEach((tab) => { const active = tab.dataset.rwView === view; tab.classList.toggle("active", active); tab.setAttribute("aria-pressed", String(active)); });
    root.querySelectorAll("[data-rw-panel]").forEach((panel) => { panel.hidden = panel.dataset.rwPanel !== view; });
  }

  $("refresh").addEventListener("click", () => state.auth ? loadSnapshot(true) : refreshStatus(true));
  $("login").addEventListener("click", startAuth); $("logout").addEventListener("click", logout); $("device-cancel").addEventListener("click", logout);
  $("filters").addEventListener("submit", (event) => event.preventDefault());
  ["search", "priority", "area", "type", "status", "platform"].forEach((id) => $(id).addEventListener(id === "search" ? "input" : "change", renderOverview));
  $("filter-reset").addEventListener("click", () => { $("filters").reset(); renderOverview(); });
  ["board", "list"].forEach((mode) => $(`${mode}-mode`).addEventListener("click", () => {
    state.mode = mode; ["board", "list"].forEach((value) => { $(`${value}-mode`).classList.toggle("active", value === mode); $(`${value}-mode`).setAttribute("aria-pressed", String(value === mode)); }); renderOverview();
  }));
  root.querySelectorAll("[data-rw-view]").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.rwView)));
  $("detail-close").addEventListener("click", () => $("detail").close());
  $("bookmark").addEventListener("click", () => { if (state.selected) { state.bookmarks.set(state.selected.id, {item: state.selected, note: ""}); renderBookmarks(); openDetail(state.selected); message(tx("Eintrag für diese Sitzung vorgemerkt.", "Entry saved for this session.")); } });
  $("export-item").addEventListener("click", () => { if (state.selected) download("pruefnotiz.md", exportHeader() + itemMarkdown(state.selected, state.snapshot?.fetched_at)); });
  $("checklist-reset").addEventListener("click", () => { state.checks = {}; saveChecks(); renderChecklist(); message(tx("Häkchen zurückgesetzt.", "Checkmarks reset.")); });
  $("export-worklist").addEventListener("click", exportWorklist);
  $("export-drafts").addEventListener("click", () => download("dokumentationsbedarf.md", exportHeader() + [...(state.snapshot?.documentation_gaps || []), ...state.drafts].map(gapMarkdown).join("\n")));
  $("draft-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("draft-form").reportValidity()) return;
    if (state.drafts.length >= 30) { message(tx("Maximal 30 lokale Entwürfe. Bitte vorhandene Entwürfe exportieren und entfernen.", "You can keep up to 30 local drafts. Export and remove existing drafts first.")); return; }
    state.drafts.push({title: $("draft-title").value.trim(), area: $("draft-area").value.trim(), cause: $("draft-cause").value.trim(), next_steps: $("draft-next").value.split("\n").map((line) => line.trim()).filter(Boolean), tests: $("draft-tests").value.trim(), documentation: $("draft-docs").value.split("\n").map((line) => line.trim()).filter(Boolean), evidence: [], assessment: tx("Lokaler Entwurf. Angaben sind ungeprüft; noch kein Issue oder Arbeitsauftrag veröffentlicht.", "Local draft. Details are unverified; no issue or work order has been published." )});
    $("draft-form").reset(); renderGaps(); message(tx("Entwurf in diesem Tab aufgenommen. Zum Aufbewahren exportieren.", "Draft added in this tab. Export it to keep a copy."));
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !state.pending) refreshStatus(true); });
  window.addEventListener("pagehide", () => { stopPoll(); invalidate(); clearTimeout(state.expiryTimer); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) refreshStatus(true); });

  try { state.checks = readChecks(localStorage); }
  catch (_error) { message(tx("Gespeicherte Häkchen nicht verfügbar; die Arbeitsliste startet leer.", "Saved checkmarks are unavailable; the worklist starts empty.")); }
  renderAll();
  refreshStatus().then((auth) => { if (auth) loadSnapshot(); });
})();
