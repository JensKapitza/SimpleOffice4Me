(function (root) {
  "use strict";

  function serialEligible(view, page) {
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(view.location?.hostname);
    return Boolean(page.dataset.usb === "1" && loopback && view.isSecureContext && view.top === view
      && view.navigator?.serial && typeof view.navigator.serial.requestPort === "function");
  }

  function boot(page) {
    const { ReactionGame, VirtualStations, keyStation, keystrokesPerMinute } = root.KidsReactionGame;
    const { UsbStations, PORT_FILTERS } = root.KidsReactionUsb;
    const en = page.dataset.en === "1";
    const t = (german, english) => en ? english : german;
    const find = (key) => page.querySelector(`[data-${key}]`);
    const game = new ReactionGame();
    const buttons = [...page.querySelectorAll("[data-station]")];
    const keyboard = find("keyboard");
    const hasUsb = serialEligible(root, page);
    let usb = null;
    let selecting = false;
    let generation = 0;
    let message = find("play-state").textContent;
    let link = hasUsb ? "virtual" : "unavailable";

    const virtual = new VirtualStations(receive);
    const transport = () => usb ?? virtual;

    function receive(event) {
      const result = game.event(event);
      if (!result) return;
      if (result.status === "target") {
        message = t(`Ziel: Station ${event.id}`, `Target: station ${event.id}`);
      } else if (result.status === "wrong") {
        message = game.phase === "retry"
          ? t("Andere Station. Starte dieselbe Reihenfolge noch einmal.", "Different station. Try the same sequence again.")
          : t("Das war eine andere Station. Das Ziel bleibt gleich.", "That was a different station. The target stays the same.");
      } else if (result.status === "miss") {
        message = game.phase === "retry"
          ? t("Die Zeit ist um. Du kannst dieselbe Reihenfolge erneut versuchen.", "Time is up. You can try that sequence again.")
          : t("Ziel verpasst.", "Target missed.");
      } else {
        message = t("Getroffen!", "Nice hit!");
      }
      if (game.phase === "change") message = t("Wechsel: Spieler 2 ist dran.", "Switch: player 2 goes next.");
      if (game.phase === "level") message = t("Stufe geschafft! Das nächste Zeitfenster ist etwas kürzer.", "Level complete! The next window is a little shorter.");
      if (game.phase === "complete") message = t("Runde abgeschlossen.", "Round complete.");
      if (result.stop) transport().off();
      if (result.next) arm(result.next);
      render();
    }

    function arm(target) {
      if (!target) return;
      message = t("Ziel wird vorbereitet …", "Setting the target …");
      transport().arm(target);
    }

    function scoreLine(score) {
      const rate = keystrokesPerMinute(score);
      return t(
        `Treffer: ${score.hits} · Falsche Drücke: ${score.errors} · Verpasst: ${score.misses} · Gesamt-Zielzeit: ${score.total} ms · Tempo: ${rate}/Min`,
        `Hits: ${score.hits} · Wrong presses: ${score.errors} · Missed: ${score.misses} · Total target time: ${score.total} ms · Pace: ${rate}/min`
      );
    }

    function renderScores() {
      find("score").textContent = scoreLine(game.score);
      const groups = game.mode === "duel"
        ? (game.results.includes(game.score) ? game.results : [...game.results, game.score])
        : [game.score];
      const rows = groups.flatMap((score, i) => score.rows.map((value) => {
        const tr = document.createElement("tr");
        const player = game.mode === "duel" ? String(i + 1) : "–";
        for (const text of [player, value.id, value.hit ? t("Treffer", "Hit") : t("Verpasst", "Missed"), `${value.ms} ms`]) {
          const td = document.createElement("td");
          td.textContent = text;
          tr.append(td);
        }
        return tr;
      }));
      find("results").replaceChildren(...rows);
      const winner = game.winner();
      const compare = game.results.map((s, i) => t(`Spieler ${i + 1}: `, `Player ${i + 1}: `) + scoreLine(s));
      if (winner !== null) compare.push(winner === 0
        ? t("Gleichstand.", "Tie.") : t(`Spieler ${winner} gewinnt.`, `Player ${winner} wins.`));
      find("compare").textContent = compare.join(" / ");
    }

    function render() {
      const busy = game.phase === "playing";
      const next = ["change", "level", "retry"].includes(game.phase);
      const target = game.active?.ack ? game.active : null;
      find("start").disabled = busy || next || selecting || Boolean(usb && !usb.ready);
      find("mode").disabled = busy || next;
      find("stop").disabled = !(busy || next);
      find("continue").hidden = !next;
      find("continue").textContent = game.phase === "change" ? t("Spieler 2 starten", "Start player 2") :
        (game.phase === "retry" ? t("Gleiche Stufe neu starten", "Retry same level") : t("Nächste Stufe starten", "Start next level"));
      find("play-state").textContent = message;
      find("target").textContent = target
        ? (usb ? t(`Leuchtende Station: ${target.id}`, `Lit station: ${target.id}`)
          : t(`Station ${target.id} · Taste ${target.id}`, `Station ${target.id} · Key ${target.id}`))
        : t("Keine aktive Station", "No active station");
      find("window").textContent = target
        ? (target.window ? t(`Zeitfenster: ${target.window / 1000} s`, `Time window: ${target.window / 1000} s`)
          : t("Kein Zeitlimit", "No time limit"))
        : "";
      const ordered = (busy || next || game.phase === "complete") && game.mode === "ordered";
      find("sequence").textContent = ordered
        ? t(`Stufe ${game.level + 1} von 4 · Folge: `, `Level ${game.level + 1} of 4 · Sequence: `) + game.task.ids.join(" → ")
        : "";
      const remaining = game.phase !== "idle" && game.phase !== "stopped";
      const progress = `${game.score.rows.length} / ${game.task.ids.length}`;
      const turn = game.mode === "duel" ? t(`Spieler ${game.player + 1} · `, `Player ${game.player + 1} · `) : "";
      find("progress").textContent = remaining ? turn + t(`Ziele abgeschlossen: ${progress}`, `Targets completed: ${progress}`) : "";
      find("reward").textContent = game.phase === "complete"
        ? t("⭐ Gut gespielt! Runde beendet.", "⭐ Well played! Round complete.")
        : (game.phase === "level" ? t("⭐ Stufe geschafft!", "⭐ Level complete!") : "");
      for (const button of buttons) {
        button.disabled = !target || Boolean(usb);
        button.classList.toggle("is-target", Number(button.dataset.station) === target?.id);
      }
      keyboard.tabIndex = usb ? -1 : 0;
      find("connect").disabled = !hasUsb || busy || next || selecting || Boolean(usb);
      find("disconnect").disabled = !usb;
      const connection = {
        virtual: t("Virtuell: Zeiten des Browsers.", "Virtual: browser timing."),
        unavailable: t("Virtuell: USB ist hier nicht verfügbar.", "Virtual: USB is not available here."),
        choosing: t("Wähle den ESP32-Port aus.", "Choose the ESP32 port."),
        opening: t("Verbindung zum ESP32 wird geprüft …", "Checking the ESP32 connection …"),
        ready: t("ESP32 verbunden: Zeiten vom Gerät (LED bis entprellter Druck).", "ESP32 connected: device timing (LED to debounced press)."),
        lost: t("USB-Verbindung verloren. Runde abgebrochen. Zum Weiterspielen neu auswählen oder virtuell starten.", "USB connection lost. Round canceled. Choose the board again or start in virtual mode."),
        unrecognized: t("Kein passender ESP32 erkannt. Starte virtuell oder prüfe Aufbau und Firmware.", "No matching ESP32 found. Start in virtual mode or check the setup and firmware."),
        closed: t("USB getrennt. Virtuell ist wieder verfügbar.", "USB disconnected. Virtual mode is available again.")
      };
      find("connection").textContent = connection[link];
      renderScores();
    }

    find("start").addEventListener("click", () => {
      if (selecting || (usb && !usb.ready)) return;
      arm(game.start(find("mode").value));
      if (!usb && game.phase === "playing") keyboard.focus();
      render();
    });
    find("continue").addEventListener("click", () => {
      arm(game.continue());
      if (!usb && game.phase === "playing") keyboard.focus();
      render();
    });
    find("stop").addEventListener("click", () => {
      transport().off();
      game.stop();
      message = t("Runde gestoppt.", "Round stopped.");
      render();
    });
    for (const button of buttons) button.addEventListener("click", () => {
      if (!usb) virtual.press(Number(button.dataset.station));
    });
    keyboard.addEventListener("keydown", (event) => {
      if (usb || game.phase !== "playing" || !game.active?.ack) return;
      const id = keyStation(event, keyboard);
      if (!id) return;
      event.preventDefault();
      virtual.press(id);
    });

    async function connect() {
      if (!hasUsb || usb || selecting || game.phase === "playing") return;
      const pending = ++generation;
      selecting = true;
      link = "choosing";
      render();
      try {
        const port = await root.navigator.serial.requestPort({ filters: PORT_FILTERS });
        if (pending !== generation || document.hidden) return;
        link = "opening";
        const stations = new UsbStations(port, receive, (state) => {
          if (usb !== stations) return;
          link = state;
          if (state !== "ready") {
            usb = null;
            game.stop();
            message = t("Runde gestoppt. Neue Runde bitte ausdrücklich starten.", "Round stopped. Start a new round explicitly.");
          }
          render();
        });
        usb = stations;
        await stations.open();
      } catch {
        if (pending === generation) {
          if (usb) usb.close("unrecognized");
          else link = "virtual";
        }
      } finally {
        selecting = false;
        render();
      }
    }

    function leave() {
      generation += 1;
      if (usb) {
        usb.off();
        usb.close("closed");
      } else if (game.phase === "playing") {
        virtual.off();
        game.stop();
        message = t("Runde gestoppt.", "Round stopped.");
      }
      if (!usb && selecting && link === "choosing") link = "virtual";
      render();
    }

    find("connect").addEventListener("click", connect);
    find("disconnect").addEventListener("click", leave);
    root.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); });
    render();
  }

  if (typeof module === "object" && module.exports) module.exports = { serialEligible };
  else {
    const page = document.querySelector("[data-kids-reaction]");
    if (page) boot(page);
  }
}(globalThis));
