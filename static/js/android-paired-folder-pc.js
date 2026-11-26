(() => {
  "use strict";
  const page = document.querySelector("[data-paired-folder-pc]");
  if (!page) return;
  const byId = (id) => document.getElementById(`paired-folder-${id}`);
  const pair = byId("pair"), code = byId("code"), state = byId("state"), search = byId("search");
  const query = byId("query"), list = byId("results"), summary = byId("summary");
  const say = (en, de) => page.dataset.lang === "en" ? en : de;
  const encoder = new TextEncoder();
  const hex = (data) => Array.from(new Uint8Array(data), (value) => value.toString(16).padStart(2, "0")).join("");
  let key = null, timer = null, version = 0, working = false, scope = "";

  const clearResults = () => {
    list.replaceChildren();
    summary.textContent = "";
  };
  const stop = (message) => {
    version += 1;
    clearTimeout(timer);
    key = null;
    working = false;
    scope = "";
    clearResults();
    query.value = code.textContent = "";
    search.hidden = true;
    pair.disabled = false;
    state.textContent = message;
  };
  const revoked = () => stop(say("Access ended or the phone cannot be reached. Enable pairing on the phone again.",
    "Zugriff beendet oder Handy nicht erreichbar. Aktiviere die Kopplung am Handy erneut."));
  const call = async (name, body) => {
    const response = await fetch(`${page.dataset.api}/${name}`, {
      method: "POST", credentials: "same-origin", redirect: "error", cache: "no-store",
      headers: {"Content-Type":"application/json", "X-CSRF-Token":page.dataset.csrf},
      body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("folder access unavailable");
    return response.json();
  };
  const recovering = async (generation) => {
    if (generation !== version) return;
    scope = "";
    query.value = "";
    clearResults();
    search.hidden = true;
    try {
      const status = (await call("status", {})).status;
      if (generation === version) {
        state.textContent = status === "awaiting_source"
          ? say("The folder is no longer shared. Approve it again on the phone.",
            "Ordner nicht mehr freigegeben. Gib ihn am Handy erneut frei.")
          : say("Checking the active share on the phone.", "Aktive Freigabe am Handy wird geprüft.");
      }
    } catch (_error) {
      if (generation === version) revoked();
    }
  };
  const signed = async (using, value) => hex(await crypto.subtle.sign(
    {name: "ECDSA", hash: "SHA-256"}, using, encoder.encode(value)));
  const check = async (generation, using) => {
    if (generation !== version || working) return;
    working = true;
    try {
      const status = (await call("status", {})).status;
      if (generation !== version) return;
      if (status === "shared") {
        const challenge = await call("start", {query:""});
        const signature = await signed(using, challenge.message);
        const heartbeat = await call("read", {query:"", challenge:challenge.challenge, signature});
        if (generation !== version) return;
        if (scope !== heartbeat.scope) {
          clearResults();
          scope = heartbeat.scope;
        }
        code.textContent = "";
        state.textContent = say("Folder shared for filename search only.", "Ordner nur für die Dateinamensuche freigegeben.");
        search.hidden = false;
      } else {
        if (scope) {
          scope = "";
          query.value = "";
          clearResults();
          search.hidden = true;
        }
        state.textContent = status === "awaiting_pair"
          ? say("Compare and confirm the code on the phone.", "Vergleiche und bestätige den Code am Handy.")
          : say("PC confirmed. Share the chosen folder separately on the phone.", "PC bestätigt. Gib den gewählten Ordner am Handy separat frei.");
      }
    } catch (_error) {
      await recovering(generation);
    } finally {
      if (generation === version) {
        working = false;
        schedule(generation, using);
      }
    }
  };
  const schedule = (generation, using) => {
    clearTimeout(timer);
    timer = setTimeout(() => check(generation, using), 3000);
  };

  pair.addEventListener("click", async () => {
    if (!window.isSecureContext || !window.crypto?.subtle) {
      stop(say("Open the phone's app via trusted HTTPS.", "Öffne die App des Handys über vertrauenswürdiges HTTPS."));
      return;
    }
    pair.disabled = true;
    working = true;
    const generation = ++version;
    try {
      const keys = await crypto.subtle.generateKey({name:"ECDSA", namedCurve:"P-256"}, false, ["sign", "verify"]);
      const publicKey = await crypto.subtle.exportKey("raw", keys.publicKey);
      const proposal = await call("begin", {public_key:hex(publicKey)});
      const digest = hex(await crypto.subtle.digest("SHA-256", publicKey));
      const signature = await signed(keys.privateKey, proposal.message);
      await call("prove", {challenge:proposal.challenge, signature});
      if (generation !== version) return;
      code.textContent = digest.match(/.{8}/g).join(" ");
      key = keys.privateKey;
      state.textContent = say("Compare and confirm the code on the phone.", "Vergleiche und bestätige den Code am Handy.");
      schedule(generation, key);
    } catch (_error) {
      if (generation === version) revoked();
    } finally {
      if (generation === version) working = false;
    }
  });

  search.addEventListener("submit", async (event) => {
    event.preventDefault();
    const term = query.value.trim();
    if (!scope || !key || term.length < 2 || term.length > 120) return;
    if (working) {
      summary.textContent = say("Checking the phone. Try again in a moment.", "Handy wird geprüft. Versuch es gleich noch einmal.");
      return;
    }
    working = true;
    const generation = version, using = key;
    clearTimeout(timer);
    clearResults();
    try {
      const challenge = await call("start", {query:term});
      const signature = await signed(using, challenge.message);
      const result = await call("read", {query:term, challenge:challenge.challenge, signature});
      if (generation !== version || document.hidden) return;
      scope = result.scope;
      for (const name of result.names) {
        const item = document.createElement("li");
        item.className = "list-group-item";
        item.textContent = name;
        list.append(item);
      }
      summary.textContent = result.limited
        ? say("Search limit reached. Results may be incomplete.", "Suchgrenze erreicht. Treffer können fehlen.")
        : result.names.length === 0 ? say("No matching file names.", "Keine passenden Dateinamen.") : "";
    } catch (_error) {
      await recovering(generation);
    } finally {
      if (generation === version) {
        working = false;
        schedule(generation, using);
      }
    }
  });

  window.addEventListener("offline", revoked);
  window.addEventListener("pagehide", revoked);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearResults();
  });
})();
