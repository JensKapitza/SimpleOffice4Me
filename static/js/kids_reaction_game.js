(function (root) {
  "use strict";

  const LEVELS = Object.freeze([
    { ids: [1, 2, 3], window: 8000 }, { ids: [2, 3, 1], window: 6500 },
    { ids: [3, 1, 2], window: 5000 }, { ids: [2, 1, 3], window: 4000 }
  ]);
  const DUEL = Object.freeze({ ids: [1, 3, 2, 3, 1, 2], window: 7000 });
  const PRACTICE = Object.freeze({ ids: [1, 2, 3], window: 0 });
  const MODE = new Set(["practice", "duel", "ordered"]);
  const station = (value) => Number.isInteger(value) && value >= 1 && value <= 3;
  const milliseconds = (value) => Number.isInteger(value) && value >= 0 && value <= 0xffffffff;
  const scoreboard = () => ({ hits: 0, errors: 0, misses: 0, total: 0, rows: [] });

  class ReactionGame {
    constructor() {
      this.ticket = 0;
      this.active = null;
      this.phase = "idle";
      this.level = 0;
      this.mode = "practice";
      this.player = 0;
      this.results = [];
      this.resetAttempt();
    }

    resetAttempt() {
      this.active = null;
      this.index = 0;
      this.score = scoreboard();
    }

    get task() {
      if (this.mode === "ordered") return LEVELS[this.level];
      return this.mode === "duel" ? DUEL : PRACTICE;
    }

    start(mode) {
      if (!MODE.has(mode) || this.phase === "playing" || this.phase === "change") return null;
      this.mode = mode;
      this.level = 0;
      this.player = 0;
      this.results = [];
      return this.startAttempt();
    }

    continue() {
      if (this.phase === "change") this.player = 1;
      else if (this.phase === "level") this.level += 1;
      else if (this.phase !== "retry") return null;
      return this.startAttempt();
    }

    startAttempt() {
      this.resetAttempt();
      this.phase = "playing";
      return this.next();
    }

    next() {
      const target = { ticket: ++this.ticket, id: this.task.ids[this.index], window: this.task.window };
      this.active = { ...target, ack: false, lastSeq: 0 };
      return target;
    }

    event(event) {
      const target = this.active;
      if (!target || this.phase !== "playing" || event.ticket !== target.ticket || !station(event.id)) return null;
      if (event.type === "target") {
        if (target.ack || target.id !== event.id || event.window !== target.window) return null;
        target.ack = true;
        return { status: "target" };
      }
      if (!target.ack || (event.seq !== undefined
        && (!Number.isInteger(event.seq) || event.seq <= target.lastSeq))) return null;
      if (event.type === "wrong") {
        if (event.id === target.id || !milliseconds(event.ms) || (target.window && event.ms >= target.window)) return null;
        if (event.seq !== undefined) target.lastSeq = event.seq;
        this.score.errors += 1;
        if (this.mode === "ordered") {
          this.active = null;
          this.phase = "retry";
        }
        return { status: "wrong", stop: this.phase === "retry" };
      }
      const hit = event.type === "hit" && milliseconds(event.ms) && (!target.window || event.ms < target.window);
      const miss = event.type === "miss" && target.window > 0;
      if ((!hit && !miss) || target.id !== event.id) return null;
      this.active = null;
      const ms = hit ? event.ms : target.window;
      this.score.total += ms;
      this.score[hit ? "hits" : "misses"] += 1;
      this.score.rows.push({ id: target.id, hit, ms });
      if (miss && this.mode === "ordered") {
        this.phase = "retry";
        return { status: "miss", stop: true };
      }
      this.index += 1;
      if (this.index < this.task.ids.length) {
        const next = this.next();
        return { status: hit ? "hit" : "miss", next };
      }
      if (this.mode === "duel") {
        this.results.push(this.score);
        this.phase = this.player === 0 ? "change" : "complete";
      } else if (this.mode === "ordered" && this.level + 1 < LEVELS.length) {
        this.phase = "level";
      } else {
        this.phase = "complete";
      }
      return { status: hit ? "hit" : "miss" };
    }

    stop() {
      const activeTicket = this.active?.ticket ?? this.ticket;
      this.active = null;
      this.phase = "stopped";
      this.results = [];
      this.score = scoreboard();
      return activeTicket;
    }

    winner() {
      if (this.results.length !== 2) return null;
      const [a, b] = this.results;
      const order = (a.hits - b.hits) || (b.misses + b.errors - a.misses - a.errors) || (b.total - a.total);
      return order === 0 ? 0 : (order > 0 ? 1 : 2);
    }
  }

  class VirtualStations {
    constructor(emit, now = () => performance.now(), schedule = setTimeout, cancel = clearTimeout) {
      Object.assign(this, { emit, now, schedule, cancel });
      this.active = null;
      this.timer = null;
    }

    arm(target) {
      this.off();
      this.active = { ...target, start: this.now() };
      if (target.window) this.timer = this.schedule(() => this.press(0), target.window);
      this.emit({ type: "target", ...target });
    }

    press(id) {
      const target = this.active;
      if (!target) return;
      const ms = Math.max(0, Math.floor(this.now() - target.start));
      if (target.window && ms >= target.window) {
        this.off();
        this.emit({ type: "miss", ticket: target.ticket, id: target.id });
        return;
      }
      if (id === 0 && target.window) {
        this.timer = this.schedule(() => this.press(0), target.window - ms);
        return;
      }
      if (!station(id)) return;
      if (id === target.id) this.off();
      this.emit({ type: id === target.id ? "hit" : "wrong", ticket: target.ticket, id, ms });
    }

    off() {
      if (this.timer !== null) this.cancel(this.timer);
      this.timer = null;
      this.active = null;
    }
  }

  const api = { ReactionGame, VirtualStations };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.KidsReactionGame = api;
}(globalThis));
