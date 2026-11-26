(function () {
  "use strict";
  const page = document.querySelector("[data-reaction-room]");
  if (!page) return;
  const { keyStation, elapsedMs } = globalThis.KidsReactionGame;
  const element = (name) => page.querySelector(`[data-room-${name}]`);
  const text = (en, de) => page.dataset.en === "1" ? en : de;
  const tickMs = 500;
  const notice = element("notice");
  const entry = element("entry");
  const play = element("play");
  const touch = element("touch");
  const keyboard = element("keyboard");
  const kickoff = element("start");
  const next = element("next");
  const leave = element("leave");
  const mode = element("mode");
  const peers = element("roster");
  const invite = element("invite");
  const stationLabel = element("station");
  const targetLabel = element("target");
  const board = element("board");
  const result = element("result");
  const times = element("times");
  let token = "";
  let room = null;
  let epoch = 0;
  let busy = false;
  let queued = null;
  let pollTimer = null;
  let cueTimer = null;
  let cue = null;
  let pressed = false;
  let drawn = null;
  let lastCueAt = -Infinity;

  const copy = (target, value) => {
    if (target.textContent !== value) target.textContent = value;
  };
  const show = (target, value) => { target.hidden = !value; };
  const languageError = (code) => {
    const messages = {
      bad_request: ["Check the room code and try again.", "Prüfe den Raumcode und versuche es erneut."],
      unavailable: ["Room not found or already started.", "Raum nicht gefunden oder bereits gestartet."],
      room_full: ["All stations in this room are occupied.", "Alle Stationen in diesem Raum sind belegt."],
      room_limit: ["Close your other room or try again shortly.", "Schließe deinen anderen Raum oder versuche es gleich erneut."],
      already_joined: ["This browser is already a station in the room.", "Dieser Browser ist bereits eine Station im Raum."],
      try_later: ["Too many attempts. Please wait.", "Zu viele Versuche. Bitte warte kurz."],
      round_limit: ["This room has reached its round limit. Open a new one.", "Dieser Raum hat sein Rundenlimit erreicht. Öffne einen neuen Raum."],
      not_ready: ["Wait for a second station and try again.", "Warte auf eine zweite Station und versuche es erneut."],
      local_https_only: ["Open the local HTTPS address and sign in.", "Öffne die lokale HTTPS-Adresse und melde dich an."],
      room_gone: ["The room has ended. Open or join a new one.", "Der Raum ist beendet. Öffne einen neuen oder tritt erneut bei."],
      disconnected: ["Connection ended. Open or join a new room.", "Verbindung beendet. Öffne einen neuen Raum oder tritt erneut bei."],
    };
    return text(...(messages[code] || messages.disconnected));
  };

  async function post(action, data, stationToken, closing = false) {
    const abort = new AbortController();
    const deadline = window.setTimeout(() => abort.abort(), 3500);
    try {
      const response = await fetch(page.dataset.api, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {
          "Content-Type": "application/json", "X-CSRF-Token": page.dataset.csrf,
          "X-Reaction-Station": stationToken,
        },
        body: JSON.stringify({ action, ...data }),
        signal: abort.signal,
        keepalive: closing,
      });
      return await response.json();
    } finally {
      window.clearTimeout(deadline);
    }
  }

  function hideCue() {
    window.clearTimeout(cueTimer);
    cue = null;
    pressed = false;
    touch.disabled = true;
    touch.dataset.active = "no";
    copy(element("prompt"), "");
  }

  function renderScore() {
    const key = JSON.stringify([room.rows, room.scores, room.turn, room.winner]);
    if (key === drawn) return;
    drawn = key;
    const groups = room.scores.length > 1 ? room.scores
      : (room.scores.length === 1 && room.turn === 2 ? [...room.scores, { rows: room.rows }] : [{ rows: room.rows }]);
    const items = [];
    for (const [index, { rows }] of groups.entries()) {
      for (const row of rows) {
        const line = document.createElement("li");
        const mark = row.hit ? text("Hit", "Treffer") : text("Missed", "Verpasst");
        const turn = room.mode === "duel" ? `${text("Turn", "Durchgang")} ${index + 1} · ` : "";
        line.textContent = `${turn}${text("Station", "Station")} ${row.id}: ${mark} · ${row.ms} ms`;
        items.push(line);
      }
    }
    times.replaceChildren(...items);
    const labels = room.scores.map((score, index) => {
      const turn = room.mode === "duel" ? `${text("Turn", "Durchgang")} ${index + 1}: ` : "";
      const rate = typeof score.rate === "number" ? `, ${score.rate}${text("/min", "/Min")}` : "";
      return `${turn}${score.hits} ${text("hits", "Treffer")}, ${score.misses} ${text("missed", "verpasst")}, ${score.total} ms${rate}`;
    });
    if (room.winner !== null) labels.push(room.winner === 0 ? text("Tie", "Gleichstand") :
      `${text("Winner: player", "Gewonnen: Spieler") } ${room.winner}`);
    copy(result, labels.join(" · ") || text("No completed rounds yet.", "Noch keine abgeschlossenen Runden."));
  }

  function render(feedback = "") {
    show(entry, !room);
    for (const name of ["active", "play", "board"]) show(element(name), room);
    if (!room) {
      hideCue();
      show(invite, false);
      for (const field of [stationLabel, peers, invite, targetLabel, touch, result]) copy(field, "");
      times.replaceChildren();
      copy(notice, feedback || text("No room is open on this browser.", "In diesem Browser ist kein Raum geöffnet."));
      return;
    }
    const host = room.role === "host";
    const lobby = room.phase === "lobby";
    copy(stationLabel, `${text("Your station:", "Deine Station:")} ${room.station}`);
    copy(peers, `${text("Connected stations:", "Verbundene Stationen:")} ${room.stations.join(", ")}`);
    show(invite, !!room.join_code);
    copy(invite, room.join_code ? `${text("Room code:", "Raumcode:")} ${room.join_code}` : "");
    show(element("controls"), host);
    const canStart = lobby || room.phase === "complete";
    mode.disabled = !canStart;
    kickoff.disabled = room.stations.length < 2;
    show(kickoff, canStart);
    copy(kickoff, lobby ? text("Start round", "Runde starten") : text("Start another round", "Weitere Runde starten"));
    show(next, room.phase === "change");
    copy(leave, host ? text("Stop room for everyone", "Raum für alle beenden") : text("Leave room", "Raum verlassen"));
    const target = room.target;
    copy(targetLabel, target ? `${text("Active station:", "Aktive Station:")} ${target.station}` :
      text("No active station", "Keine aktive Station"));
    const ownTarget = target && target.station === room.station;
    if (!ownTarget || (cue && target.ticket !== cue.ticket)) hideCue();
    if (!cue && ownTarget) {
      cue = { ...target, now: null };
      wake("ack", { ticket: target.ticket });
    }
    if (!cue || cue.now === null) copy(touch, `${text("Wait:", "Warte:")} ${text("Station", "Station")} ${room.station}`);
    if (feedback) copy(notice, feedback);
    else if (lobby) copy(notice, text("Waiting in the room.", "Warten im Raum."));
    else if (room.phase === "change") copy(notice, text("Swap players. The host starts turn 2.", "Wechselt die Spieler. Der Host startet Durchgang 2."));
    else if (room.phase === "complete") copy(notice, text("Round complete!", "Runde geschafft!"));
    else copy(notice, room.mode === "duel" ? `${text("Turn", "Durchgang")} ${room.turn}` : text("Practice round", "Übungsrunde"));
    renderScore();
  }

  function arm(ticket, currentEpoch) {
    const pendingCue = cue;
    if (!pendingCue || pendingCue.ticket !== ticket) return;
    const paint = () => window.requestAnimationFrame(() => {
      if (currentEpoch !== epoch || document.hidden || !cue || cue !== pendingCue) return;
      const cueText = `${text("Touch me", "Berühre mich")} · ${text("Key", "Taste")} ${room.station}`;
      copy(touch, cueText);
      copy(element("prompt"), cueText);
      touch.dataset.active = "yes";
      touch.disabled = false;
      pendingCue.now = performance.now();
      lastCueAt = pendingCue.now;
      if (pendingCue.window) {
        cueTimer = window.setTimeout(() => {
          if (cue === pendingCue) {
            touch.disabled = true;
            touch.dataset.active = "no";
            pressed = true;
            copy(touch, text("Time is up", "Zeit abgelaufen"));
          }
        }, pendingCue.window);
      }
    });
    const pause = lastCueAt + 650 - performance.now();
    if (pause > 0) cueTimer = window.setTimeout(paint, pause);
    else paint();
  }

  function retire(feedback = "") {
    const previousToken = token;
    const action = room?.role === "host" ? "stop" : "leave";
    epoch++;
    token = "";
    room = null;
    queued = null;
    busy = false;
    drawn = null;
    hideCue();
    window.clearTimeout(pollTimer);
    render(feedback);
    if (previousToken) void post(action, {}, previousToken, true).catch(() => {
      // The server's short visibility/freshness lease expires regardless.
    });
  }

  async function send(action, data = {}) {
    if (document.hidden) return;
    const currentEpoch = epoch;
    const oldToken = token;
    busy = true;
    window.clearTimeout(pollTimer);
    try {
      const snapshot = await post(action, data, oldToken);
      if (currentEpoch !== epoch || document.hidden) {
        if (snapshot.token) void post("leave", {}, snapshot.token, true).catch(() => { /* short lease */ });
        return;
      }
      if (snapshot.error) {
        if (!oldToken || ["not_ready", "round_limit", "stale"].includes(snapshot.error)) {
          if (snapshot.error === "stale" && action === "ack") {
            retire(languageError("disconnected"));
            return;
          }
          render(snapshot.error === "stale" ? text("That target has passed.", "Dieses Ziel ist schon vorbei.") :
            languageError(snapshot.error));
        } else retire(languageError(snapshot.error));
        return;
      }
      if (snapshot.token) token = snapshot.token;
      if (!token || !snapshot.station) {
        retire(languageError("disconnected"));
        return;
      }
      room = snapshot;
      render();
      if (action === "ack") arm(data.ticket, currentEpoch);
      if (!oldToken) keyboard.focus({ preventScroll: true });
    } catch {
      if (currentEpoch === epoch) retire(languageError("disconnected"));
    } finally {
      if (currentEpoch === epoch) {
        busy = false;
        if (queued) {
          const [following, fields] = queued;
          queued = null;
          void send(following, fields);
        } else if (token && !document.hidden) {
          pollTimer = window.setTimeout(() => { void send("state"); }, tickMs);
        }
      }
    }
  }

  function wake(action, data = {}) {
    if (document.hidden) return;
    if (!busy) { void send(action, data); return; }
    // A valid action can follow an in-flight room refresh; don't issue two
    // simultaneous state-mutating requests or overwrite an enrolled session.
    if (token && !queued) queued = [action, data];
  }

  function press() {
    if (document.hidden || !cue || cue.now === null || pressed || touch.disabled) return;
    const ms = elapsedMs(cue.now);
    if ((cue.window && ms >= cue.window) || ms > 120000) return;
    pressed = true;
    touch.disabled = true;
    touch.dataset.active = "no";
    copy(touch, text("Pressed. Wait for the next station.", "Gedrückt. Warte auf die nächste Station."));
    wake("press", { ticket: cue.ticket, ms });
  }

  element("host").addEventListener("click", () => wake("host"));
  element("join").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = element("code");
    const code = input.value.trim().toUpperCase();
    if (!/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/.test(code)) {
      copy(notice, languageError("bad_request"));
      return;
    }
    input.value = "";
    wake("join", { code });
  });
  kickoff.addEventListener("click", () => wake("start", { mode: mode.value }));
  next.addEventListener("click", () => wake("next"));
  leave.addEventListener("click", () => retire(text("Room closed on this browser.", "Raum in diesem Browser geschlossen.")));
  touch.addEventListener("click", press);
  keyboard.addEventListener("keydown", (event) => {
    const focus = event.target === touch ? touch : keyboard;
    if (!room || keyStation(event, focus) !== room.station) return;
    event.preventDefault();
    press();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && (token || busy)) retire(languageError("disconnected"));
  });
  window.addEventListener("pagehide", () => {
    if (token || busy) retire(languageError("disconnected"));
  });
}());
