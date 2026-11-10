(function () {
  "use strict";

  const STATUS = {open: "Offen", review: "In Prüfung", blocked: "Blockiert", ready: "Bereit zum Merge", done: "Erledigt"};
  const TYPES = {pr: "Pull Request", issue: "Issue", ci: "CI / Test", security: "Security", review: "Review"};
  const TESTS = {success: "Erfolgreich", failed: "Fehlgeschlagen", running: "Läuft", unknown: "Ungeprüft", cancelled: "Abgebrochen"};
  const SOURCE_STATES = {ok: "Abgerufen", partial: "Teilweise", error: "Fehler", unavailable: "Nicht verfügbar"};
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
      return url ? `- [${md(entry.label || "Quelle")}](${url.replace(/\)/g, "%29")})` : "";
    }).filter(Boolean).join("\n") || "Keine belegte Quelle hinterlegt.";
  }

  function itemMarkdown(item, fetchedAt) {
    return `## ${md(item.title)}\n\n`
      + `Typ: ${md(TYPES[item.type] || item.type)} · Priorität: ${md(item.priority)} · Status: ${md(STATUS[item.status] || item.status)}\n\n`
      + `Bereich: ${md(item.area)}\n\nDatenstand: ${md(fetchedAt || "unbekannt")}\n\n`
      + `### Ursache / offene Frage\n\n${md(item.cause || "Ursache ungeprüft.")}\n\n`
      + `### Bekannte Blocker\n\n${lines(item.blockers, "Keine bestätigten Blocker erfasst; das ist kein Freigabenachweis.")}\n\n`
      + `### Nächste Prüfschritte\n\n${lines(item.next_steps, "Prüfschritte noch festlegen.")}\n\n`
      + `### Teststatus\n\n${md(item.test_status?.summary || "Ungeprüft")}\n\n`
      + `### Benötigte Dokumentation\n\n${lines(item.documentation, "Dokumentationsbedarf prüfen.")}\n\n`
      + `### Einschätzung / Unsicherheit\n\n${md(item.assessment || "Noch nicht bewertet.")}\n\n`
      + `### Quellen\n\n${evidenceMarkdown([{label: "GitHub-Eintrag", url: item.url}, ...(item.evidence || [])])}\n`;
  }

  function gapMarkdown(gap) {
    return `## ${md(gap.title)}\n\nBereich: ${md(gap.area || "Noch zuzuordnen")}\n\n`
      + `### Offene Frage\n\n${md(gap.cause)}\n\n### Nächste Prüfschritte\n\n${lines(gap.next_steps, "Noch festzulegen.")}\n\n`
      + `### Teststatus\n\n${md(gap.tests || "Noch ungeprüft.")}\n\n`
      + `### Benötigte Dokumentation\n\n${lines(gap.documentation, "Noch festzulegen.")}\n\n`
      + `### Einschätzung\n\n${md(gap.assessment || "Lokaler Entwurf, noch nicht geprüft und nicht als Issue veröffentlicht.")}\n\n`
      + `### Quellen / vorhandene Tracker\n\n${evidenceMarkdown(gap.evidence)}\n`;
  }

  function readChecks(storage) {
    const value = JSON.parse(storage.getItem(STORAGE_KEY) || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Ungültige Checkliste");
    return Object.fromEntries(Object.entries(value).filter(([key, checked]) => /^[a-zA-Z0-9_-]{1,80}$/.test(key) && checked === true));
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {filterItems, safeRepositoryUrl, itemMarkdown, gapMarkdown, readChecks};
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
    return Number.isNaN(date.valueOf()) ? "unbekannt" : date.toLocaleString("de-DE", {dateStyle: "medium", timeStyle: "short"});
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
      const link = sourceLink(entry.label || "GitHub-Quelle", entry.url);
      if (link && !seen.has(link.href)) { seen.add(link.href); box.append(link); }
    });
    if (!box.childNodes.length) box.append(empty("Keine belegte Quelle hinterlegt."));
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
      try { data = await response.json(); } catch (_error) { throw new Error("Der Server lieferte keine lesbare Antwort. Bitte die Seite neu laden."); }
      if (!response.ok) {
        const error = new Error(response.status === 401 ? "GitHub-Anmeldung ist nicht mehr gültig."
          : response.status === 403 ? "Anfrage nicht freigegeben. Bitte die Seite neu laden und erneut versuchen."
          : response.status === 429 ? "Abruflimit erreicht. Bitte später erneut aktualisieren."
            : "Abruf nicht verfügbar. Bitte später erneut versuchen.");
        error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Abruf abgebrochen oder Zeitlimit erreicht.");
      if (error instanceof TypeError) throw new Error("Verbindung zum Server fehlgeschlagen. Bitte die Verbindung prüfen.");
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
    $("auth-title").textContent = "Kontostatus wird geprüft";
    $("auth-expiry").textContent = ""; $("auth-reason").textContent = "";
    $("auth-description").textContent = "Kontodaten werden erst nach erneuter Prüfung der Sitzung angezeigt.";
    $("login").disabled = true; $("logout").hidden = true;
    $("updated").textContent = "Daten werden für den aktuellen Kontokontext neu geladen.";
    renderAll();
  }

  function applyAuth(auth) {
    if (!auth || typeof auth.context_id !== "string") throw new Error("Kontostatus konnte nicht geprüft werden.");
    const changed = state.auth && (state.auth.context_id !== auth.context_id || Boolean(state.auth.authenticated) !== Boolean(auth.authenticated));
    if (changed) invalidate();
    state.auth = auth;
    $("auth-title").textContent = auth.authenticated ? `Kontoansicht · ${auth.login || "GitHub"}` : "Öffentliche Ansicht";
    $("auth-description").textContent = auth.authenticated
      ? "Zusätzliche Daten bleiben an diese Sitzung gebunden. Sichtbar ist nur, was dein Konto und die GitHub App lesen dürfen."
      : "Öffentliche Repository-Daten sind ohne Konto lesbar. Ein GitHub-Konto kann zusätzlich freigegebene Prüf- und Security-Daten sichtbar machen.";
    $("login").hidden = Boolean(auth.authenticated);
    $("login").disabled = !auth.can_authenticate || state.pending;
    $("logout").hidden = !auth.authenticated;
    $("auth-reason").textContent = auth.reason || "";
    $("auth-reason").hidden = !auth.reason;
    $("auth-expiry").textContent = auth.authenticated && auth.expires_at ? `Anmeldung bis ${dateText(auth.expires_at)}` : "";
    clearTimeout(state.expiryTimer);
    const ttl = new Date(auth.expires_at || "").getTime() - Date.now();
    if (auth.authenticated && Number.isFinite(ttl)) {
      state.expiryTimer = setTimeout(() => {
        invalidate();
        message("Anmeldung abgelaufen. Der Kontostatus wird neu geprüft.");
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
    $("refresh").title = remaining > 0 ? `Nächster Abruf ab ${dateText(state.snapshot.next_refresh_at)}` : "GitHub-Daten erneut lesen";
    $("refresh").setAttribute("aria-describedby", "rw-refresh-hint");
    $("refresh-hint").textContent = remaining > 0 ? `GitHub-Abruflimit: nächste Aktualisierung ab ${dateText(state.snapshot.next_refresh_at)}.` : "";
    clearTimeout(state.cooldownTimer);
    if (remaining > 0) state.cooldownTimer = setTimeout(refreshButton, Math.min(remaining + 100, 2147483647));
  }

  async function loadSnapshot(force = false) {
    if (state.loading) return;
    const epoch = state.epoch; state.loading = true; refreshButton(); message("Repository-Daten werden gelesen …");
    try {
      const data = await request(force ? "/refresh" : "/snapshot", force ? "POST" : "GET");
      if (epoch !== state.epoch) return;
      if (!Array.isArray(data.items) || !Array.isArray(data.sources)) throw new Error("Die Repository-Daten sind unvollständig. Bitte erneut abrufen.");
      applyAuth(data.auth);
      if ((data.mode === "account") !== Boolean(state.auth.authenticated)) { message("Kontokontext hat sich geändert. Bitte erneut abrufen."); return; }
      state.snapshot = data;
      renderAll();
      $("updated").textContent = `Stand: ${dateText(data.fetched_at)}${data.cached ? " · Zwischengespeicherter Abruf" : ""} · ${data.mode === "account" ? "Kontoansicht" : "Öffentlich"}`;
      $("updated").title = data.fetched_at || "";
      message("Abruf abgeschlossen.");
    } catch (error) {
      if (epoch === state.epoch && error.status === 401) {
        const wasAccount = state.auth?.authenticated || state.snapshot?.mode === "account";
        invalidate(); message("GitHub-Anmeldung nicht mehr gültig. Kontodaten und lokale Kontonotizen wurden entfernt.");
        if (wasAccount) await refreshStatus(true);
      } else if (epoch === state.epoch) {
        message(`${error.message}${state.snapshot ? " Angezeigte Daten behalten ihren bisherigen Stand." : ""}`);
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
      message("GitHub-Sitzung beendet. Lokale Kontonotizen wurden entfernt.");
    } catch (error) { if (epoch === state.epoch) message(`Abmeldung nicht bestätigt. ${error.message}`); }
    finally { $("logout").disabled = false; }
  }
  async function startAuth() {
    if (state.pending || !state.auth?.can_authenticate) return;
    stopPoll(); state.pending = true; $("login").disabled = true;
    const epoch = state.authEpoch;
    try {
      const result = await request("/auth/start", "POST");
      if (epoch !== state.authEpoch) return;
      if (result.status !== "pending" || result.verification_uri !== "https://github.com/login/device" || !result.user_code) throw new Error("GitHub-Anmeldung konnte nicht vorbereitet werden.");
      $("device").hidden = false; $("device-code").value = result.user_code;
      $("device-status").textContent = `Warte auf Bestätigung bei GitHub. Code gültig bis ${dateText(result.expires_at)}.`;
      $("device-code").focus();
      schedulePoll(result.interval, epoch, new Date(result.expires_at).getTime());
    } catch (error) { if (epoch === state.authEpoch) { stopPoll(); message(error.message); } }
  }
  function schedulePoll(interval, epoch, expiresAt) {
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) { stopPoll(); message("Anmeldecode abgelaufen. Du kannst eine neue Anmeldung starten."); return; }
    const delay = Math.min(expiresAt - Date.now(), Math.max(5, Number(interval) || 5) * 1000);
    state.pollTimer = setTimeout(() => pollAuth(epoch, expiresAt), delay);
  }
  async function pollAuth(epoch, expiresAt) {
    if (epoch !== state.authEpoch || !state.pending) return;
    if (expiresAt <= Date.now()) { stopPoll(); message("Anmeldecode abgelaufen. Bitte bei Bedarf erneut anmelden."); return; }
    try {
      const result = await request("/auth/poll", "POST");
      if (epoch !== state.authEpoch) return;
      if (["pending", "slow_down"].includes(result.status)) {
        $("device-status").textContent = result.status === "slow_down" ? "GitHub begrenzt die Abfragen. Die nächste Prüfung erfolgt später." : "Warte auf Bestätigung bei GitHub …";
        schedulePoll(result.interval, epoch, expiresAt);
      } else if (result.status === "authenticated") {
        stopPoll(); invalidate(); const auth = await refreshStatus(); if (auth) await loadSnapshot();
        message("GitHub-Anmeldung bestätigt. Berechtigte Daten wurden neu angefordert.");
      } else {
        stopPoll(); message(result.status === "denied" ? "GitHub-Anmeldung wurde nicht freigegeben." : result.status === "expired" ? "Anmeldecode abgelaufen. Bitte bei Bedarf erneut anmelden." : "GitHub-Anmeldung fehlgeschlagen. Bitte erneut versuchen.");
      }
    } catch (error) { if (epoch === state.authEpoch) { stopPoll(); message(error.message); } }
  }

  function currentFilters() { return {q: $("search").value, priority: $("priority").value, area: $("area").value, type: $("type").value, status: $("status").value}; }
  function fillAreas() {
    const select = $("area"), value = select.value;
    select.replaceChildren(new Option("Alle Bereiche", ""));
    [...new Set((state.snapshot?.items || []).map((item) => item.area).filter(Boolean))].sort().forEach((area) => select.add(new Option(area, area)));
    if ([...select.options].some((option) => option.value === value)) select.value = value;
  }
  function itemBadges(item) {
    const box = element("div", "rw-actions");
    box.append(badge(item.priority || "P3", `rw-priority-${String(item.priority || "P3").toLowerCase()}`), badge(STATUS[item.status] || "Ungeprüft"), badge(item.area || "Zuordnung offen"));
    return box;
  }
  function itemCard(item) {
    const card = button("", () => openDetail(item), "rw-entry");
    const top = element("span", "rw-entry-top");
    top.append(element("span", "rw-entry-meta", `${TYPES[item.type] || "Eintrag"}${item.number ? ` #${item.number}` : ""}`), badge(item.priority || "P3", `rw-priority-${String(item.priority || "P3").toLowerCase()}`));
    card.append(top, element("span", "rw-entry-title", item.title));
    const bottom = element("span", "rw-entry-bottom"); bottom.append(badge(item.area || "Zuordnung offen")); card.append(bottom);
    card.append(element("span", "rw-entry-test", `Tests: ${item.test_status?.summary || "Ungeprüft"}`));
    return card;
  }
  function renderOverview() {
    const all = state.snapshot?.items || [], items = filterItems(all, currentFilters());
    $("results").textContent = state.snapshot ? `${items.length} von ${all.length} erfassten Einträgen` : "Noch keine bestätigten Repository-Daten.";
    $("board").replaceChildren();
    Object.entries(STATUS).forEach(([status, title]) => {
      const selected = items.filter((item) => item.status === status), column = element("section", "rw-column");
      const heading = element("h3", "rw-column-heading", title); heading.append(element("span", "rw-column-count", selected.length));
      const body = element("div", "rw-column-body");
      selected.forEach((item) => body.append(itemCard(item)));
      if (!selected.length) body.append(empty(status === "ready" ? "Keine belegte Merge-Bereitschaft." : "Keine Einträge in dieser Auswahl."));
      column.append(heading, body); $("board").append(column);
    });
    const table = element("table", "table rw-table");
    const caption = element("caption", "visually-hidden", "Gefilterte Repository-Einträge nach Priorität");
    const head = element("thead"), row = element("tr");
    ["Priorität", "Eintrag", "Bereich", "Status", "Teststatus"].forEach((label) => { const cell = element("th", "", label); cell.scope = "col"; row.append(cell); }); head.append(row);
    const body = element("tbody");
    items.forEach((item) => {
      const row = element("tr"), priority = element("td"), title = element("td");
      priority.append(badge(item.priority, `rw-priority-${String(item.priority).toLowerCase()}`));
      title.append(button(`${item.number ? `#${item.number} · ` : ""}${item.title}`, () => openDetail(item), "rw-row-button"), element("div", "small text-secondary", TYPES[item.type] || "Eintrag"));
      row.append(priority, title, element("td", "", item.area), element("td", "", STATUS[item.status]), element("td", "", item.test_status?.summary || "Ungeprüft")); body.append(row);
    });
    table.append(caption, head, body); $("list").replaceChildren(items.length ? table : empty("Keine Einträge in dieser Auswahl."));
    $("board").hidden = state.mode !== "board"; $("list").hidden = state.mode !== "list";
  }
  function renderStats() {
    const items = state.snapshot?.items || [], box = $("stats"); box.replaceChildren(); box.hidden = !state.snapshot;
    const stats = [[items.filter((item) => item.type === "pr" && item.status !== "done").length, "Offene PRs erfasst"], [items.filter((item) => item.type === "issue" && item.status !== "done").length, "Offene Issues erfasst"], [items.filter((item) => item.status === "blocked").length, "Als blockiert eingeordnet"], [items.filter((item) => ["security", "review"].includes(item.type) && item.status !== "done").length, "Security- / Review-Funde erfasst"]];
    const securitySources = (state.snapshot?.sources || []).filter((source) => /security|scanning|dependabot/.test(source.id));
    const securityIncomplete = !securitySources.length || securitySources.some((source) => source.state !== "ok");
    stats.forEach(([count, label], index) => {
      const card = element("div", "rw-stat");
      card.append(element("strong", "", index === 3 && !count && securityIncomplete ? "Ungeprüft" : count), element("span", "", label));
      if (index === 3 && securityIncomplete) card.append(element("div", "small text-secondary mt-1", "Security-Abdeckung unvollständig"));
      box.append(card);
    });
    const partial = (state.snapshot?.sources || []).filter((source) => source.state !== "ok");
    $("warning").hidden = !partial.length;
    $("warning").textContent = partial.length ? `${partial.length} Quelle(n) nur teilweise oder nicht geprüft. Zähler zeigen ausschließlich erfasste Einträge; Details unter „Quellen & Abdeckung“.` : "";
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
    body.append(detailSection("Ursache / offene Frage", item.cause, "Ursache ungeprüft."), detailSection("Bekannte Blocker", item.blockers, "Keine bestätigten Blocker erfasst. Das ist kein Freigabenachweis."), detailSection("Nächste Prüfschritte", item.next_steps, "Prüfschritte noch festlegen."), detailSection("Teststatus", `${TESTS[item.test_status?.state] || "Ungeprüft"} · ${item.test_status?.summary || "Kein belastbarer Testnachweis vorhanden."}`, "Ungeprüft"), detailSection("Benötigte Dokumentation", item.documentation, "Dokumentationsbedarf prüfen."), detailSection("Einschätzung / Unsicherheit", item.assessment, "Noch nicht bewertet."));
    if (item.head_sha) body.append(detailSection("Beobachteter Commit", item.head_sha, "Unbekannt"));
    const sources = element("section", "rw-detail-section"); sources.append(element("h3", "", "Quellen"), evidenceLinks([{label: "Eintrag bei GitHub", url: item.url}, ...(item.evidence || [])])); body.append(sources);
    body.append(element("p", "small text-secondary mt-3", `Abrufstand: ${dateText(state.snapshot?.fetched_at)} · Letzte Änderung: ${dateText(item.updated_at)}`));
    $("bookmark").textContent = state.bookmarks.has(item.id) ? "In Arbeitsliste vorgemerkt" : "Für Arbeitsliste vormerken";
    $("bookmark").disabled = state.bookmarks.has(item.id);
    if (!$("detail").open) $("detail").showModal();
  }

  function saveChecks() {
    const allowed = new Set((state.snapshot?.checklist || []).map((entry) => entry.id));
    const safe = Object.fromEntries(Object.entries(state.checks).filter(([key, checked]) => allowed.has(key) && checked === true));
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(safe)); }
    catch (_error) { message("Häkchen können in diesem Browser nicht gespeichert werden. Export bleibt verfügbar."); }
  }
  function renderChecklist() {
    const box = $("checklist"); box.replaceChildren();
    (state.snapshot?.checklist || []).forEach((entry, index) => {
      const row = element("div", "rw-check"), input = element("input", ""); input.type = "checkbox"; input.id = `rw-step-${index}`; input.checked = Boolean(state.checks[entry.id]);
      input.addEventListener("change", () => { state.checks[entry.id] = input.checked; saveChecks(); });
      const label = element("label", ""); label.htmlFor = input.id; label.append(element("strong", "", entry.title), element("small", "", entry.detail)); row.append(input, label); box.append(row);
    });
    if (!box.childNodes.length) box.append(empty("Die Arbeitsliste wird mit dem Repository-Abruf geladen."));
  }
  function renderBookmarks() {
    const box = $("bookmarks"); box.replaceChildren();
    state.bookmarks.forEach((entry, id) => {
      const card = element("article", "card rw-note-card"), title = element("h3", "", entry.item.title);
      const label = element("label", "form-label small", "Prüfnotiz · nur in diesem Tab"), note = element("textarea", "form-control mb-3");
      note.id = `rw-note-${box.childNodes.length}`; note.rows = 3; note.maxLength = 6000; note.value = entry.note; label.htmlFor = note.id; note.addEventListener("input", () => { entry.note = note.value; });
      const actions = element("div", "rw-actions"); actions.append(button("Details", () => openDetail(entry.item)), button("Vormerkung entfernen", () => { state.bookmarks.delete(id); renderBookmarks(); }));
      card.append(title, itemBadges(entry.item), detailSection("Nächste Prüfschritte", entry.item.next_steps, "Noch festzulegen."), label, note, actions); box.append(card);
    });
    if (!box.childNodes.length) box.append(empty("Noch keine Einträge vorgemerkt."));
  }
  function gapCard(gap, local = false) {
    const card = element("article", "card rw-note-card"); card.append(element("h3", "", gap.title), badge(gap.area || "Bereich offen"), detailSection("Offene Frage", gap.cause, "Noch zu prüfen."), detailSection("Nächste Prüfschritte", gap.next_steps, "Noch festzulegen."), detailSection("Benötigte Dokumentation", gap.documentation, "Noch festzulegen."));
    if (gap.tests) card.append(detailSection("Teststatus", gap.tests, "Ungeprüft"));
    card.append(element("p", "text-secondary mt-3", gap.assessment || "Lokaler Entwurf, ungeprüft und nicht als Issue veröffentlicht."), evidenceLinks(gap.evidence));
    const actions = element("div", "rw-actions mt-3"); actions.append(button("Entwurf exportieren", () => download("arbeitsauftrag.md", exportHeader() + gapMarkdown(gap))));
    if (local) actions.append(button("Entwurf entfernen", () => { state.drafts = state.drafts.filter((item) => item !== gap); renderGaps(); }));
    card.append(actions); return card;
  }
  function renderGaps() {
    $("gap-list").replaceChildren(); $("local-drafts").replaceChildren();
    (state.snapshot?.documentation_gaps || []).forEach((gap) => $("gap-list").append(gapCard(gap)));
    if (!$("gap-list").childNodes.length) $("gap-list").append(empty(state.snapshot ? "Keine Dokumentationshinweise im erfassten Bestand. Weitere offene Punkte kannst du als lokalen Entwurf aufnehmen." : "Noch keine Dokumentationshinweise geladen."));
    state.drafts.forEach((gap) => $("local-drafts").append(gapCard(gap, true)));
  }
  function renderSources() {
    const box = $("source-list"); box.replaceChildren();
    (state.snapshot?.sources || []).forEach((source) => {
      const row = element("article", "rw-source"), title = element("div", "fw-semibold", source.label);
      const time = element("time", "", dateText(source.checked_at)); if (source.checked_at) time.dateTime = source.checked_at; title.append(time);
      row.append(title, badge(SOURCE_STATES[source.state] || "Ungeprüft", source.state === "ok" ? "rw-badge-readonly" : "rw-priority-p2"), element("p", "mb-0", source.message || "Keine weiteren Angaben.")); box.append(row);
    });
    if (!box.childNodes.length) box.append(empty("Noch kein Quellenabruf bestätigt."));
  }
  function renderAll() { fillAreas(); renderStats(); renderOverview(); renderChecklist(); renderBookmarks(); renderGaps(); renderSources(); }

  function exportHeader() { return `# SimpleOffice4Me · Arbeitszentrale\n\nNur Planung und Dokumentation. Keine GitHub-Aktionen ausgeführt.\n\nDatenstand: ${md(state.snapshot?.fetched_at || "unbekannt")} · Ansicht: ${state.snapshot?.mode === "account" ? "kontogebunden; kann nicht öffentliche Informationen enthalten" : "öffentlich"}\n\n`; }
  function download(filename, content) {
    const url = URL.createObjectURL(new Blob([content], {type: "text/markdown;charset=utf-8"}));
    const link = element("a", ""); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    message("Markdown-Export erstellt.");
  }
  function exportWorklist() {
    let content = exportHeader() + "## Wiederverwendbare Arbeitsliste\n\n";
    (state.snapshot?.checklist || []).forEach((entry) => { content += `- [${state.checks[entry.id] ? "x" : " "}] ${md(entry.title)} — ${md(entry.detail)}\n`; });
    state.bookmarks.forEach((entry) => { content += `\n${itemMarkdown(entry.item, state.snapshot?.fetched_at)}\n### Lokale Prüfnotiz\n\n${md(entry.note || "Noch keine Notiz.")}\n`; });
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
  ["search", "priority", "area", "type", "status"].forEach((id) => $(id).addEventListener(id === "search" ? "input" : "change", renderOverview));
  $("filter-reset").addEventListener("click", () => { $("filters").reset(); renderOverview(); });
  ["board", "list"].forEach((mode) => $(`${mode}-mode`).addEventListener("click", () => {
    state.mode = mode; ["board", "list"].forEach((value) => { $(`${value}-mode`).classList.toggle("active", value === mode); $(`${value}-mode`).setAttribute("aria-pressed", String(value === mode)); }); renderOverview();
  }));
  root.querySelectorAll("[data-rw-view]").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.rwView)));
  $("detail-close").addEventListener("click", () => $("detail").close());
  $("bookmark").addEventListener("click", () => { if (state.selected) { state.bookmarks.set(state.selected.id, {item: state.selected, note: ""}); renderBookmarks(); openDetail(state.selected); message("Eintrag für diese Sitzung vorgemerkt."); } });
  $("export-item").addEventListener("click", () => { if (state.selected) download("pruefnotiz.md", exportHeader() + itemMarkdown(state.selected, state.snapshot?.fetched_at)); });
  $("checklist-reset").addEventListener("click", () => { state.checks = {}; saveChecks(); renderChecklist(); message("Häkchen zurückgesetzt."); });
  $("export-worklist").addEventListener("click", exportWorklist);
  $("export-drafts").addEventListener("click", () => download("dokumentationsbedarf.md", exportHeader() + [...(state.snapshot?.documentation_gaps || []), ...state.drafts].map(gapMarkdown).join("\n")));
  $("draft-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("draft-form").reportValidity()) return;
    if (state.drafts.length >= 30) { message("Maximal 30 lokale Entwürfe. Bitte vorhandene Entwürfe exportieren und entfernen."); return; }
    state.drafts.push({title: $("draft-title").value.trim(), area: $("draft-area").value.trim(), cause: $("draft-cause").value.trim(), next_steps: $("draft-next").value.split("\n").map((line) => line.trim()).filter(Boolean), tests: $("draft-tests").value.trim(), documentation: $("draft-docs").value.split("\n").map((line) => line.trim()).filter(Boolean), evidence: [], assessment: "Lokaler Entwurf. Angaben sind ungeprüft; noch kein Issue oder Arbeitsauftrag veröffentlicht."});
    $("draft-form").reset(); renderGaps(); message("Entwurf in diesem Tab aufgenommen. Zum Aufbewahren exportieren.");
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !state.pending) refreshStatus(true); });
  window.addEventListener("pagehide", () => { stopPoll(); invalidate(); clearTimeout(state.expiryTimer); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) refreshStatus(true); });

  try { state.checks = readChecks(localStorage); }
  catch (_error) { message("Gespeicherte Häkchen nicht verfügbar; die Arbeitsliste startet leer."); }
  renderAll();
  refreshStatus().then((auth) => { if (auth) loadSnapshot(); });
})();
