(function (root) {
  "use strict";
  const MAX_LINE = 96;
  const MAX_U32 = 0xffffffff;
  const WATCHDOG_MS = 2300;
  const AUTH_WAIT_MS = 5500;
  const TARGET_WAIT_MS = 1500;
  const PORT_OPTIONS = { baudRate: 115200, flowControl: "none", bufferSize: 256 };
  const PORT_FILTERS = [
    { usbVendorId: 0x10c4, usbProductId: 0xea60 },
    { usbVendorId: 0x1a86, usbProductId: 0x7523 },
    { usbVendorId: 0x0403, usbProductId: 0x6001 }
  ];

  function integer(value, min = 0, max = MAX_U32) {
    if (!/^(0|[1-9][0-9]{0,9})$/.test(value ?? "")) return null;
    const result = Number(value);
    return result >= min && result <= max ? result : null;
  }

  function parsed(line, epoch) {
    const fields = line.split(" ");
    const [word, token, a, b, c, d] = fields;
    if (token !== epoch) return null;
    if (word === "READY" && fields.length === 3 && a === "1") return { type: "ready" };
    if (word === "PONG" && fields.length === 3) {
      const seq = integer(a, 1);
      return seq === null ? null : { type: "pong", seq };
    }
    const ticket = integer(a, 1);
    const id = integer(b, 1, 3);
    if (ticket === null || id === null) return null;
    if (word === "TARGET" && fields.length === 5) {
      const window = integer(c, 0, 12000);
      return window === null ? null : { type: "target", ticket, id, window };
    }
    if (word === "MISS" && fields.length === 4) return { type: "miss", ticket, id };
    if ((word === "HIT" || word === "WRONG") && fields.length === 6) {
      const ms = integer(c);
      const seq = integer(d, 1);
      if (ms !== null && seq !== null) return { type: word.toLowerCase(), ticket, id, ms, seq };
    }
    return null;
  }

  class Lines {
    constructor(output) {
      this.output = output;
      this.line = "";
      this.drop = false;
    }

    feed(input) {
      for (const byte of input) {
        if (byte === 10) {
          if (!this.drop && this.line) this.output(this.line);
          this.drop = false;
          this.line = "";
        } else if (byte !== 13 && !this.drop) {
          if (byte < 32 || byte > 126 || this.line.length >= MAX_LINE) this.drop = true;
          else this.line += String.fromCharCode(byte);
        }
      }
    }
  }

  function epochFromCrypto() {
    return Array.from(crypto.getRandomValues(new Uint8Array(8)), (n) => n.toString(16).padStart(2, "0")).join("");
  }

  class UsbStations {
    constructor(port, event, status, clocks = {}) {
      Object.assign(this, { port, event, status });
      this.now = clocks.now ?? (() => performance.now());
      this.interval = clocks.interval ?? setInterval;
      this.clear = clocks.clear ?? clearInterval;
      this.epoch = clocks.epoch ?? epochFromCrypto();
      this.ready = false;
      this.closed = false;
      this.lastTicket = 0;
      this.armTarget = null;
      this.frameSeq = 0;
      this.pingSeq = 0;
      this.ackSeq = 0;
      this.writes = 0;
      this.lines = new Lines((line) => this.onLine(line));
      this.onDisconnect = () => this.close("lost");
    }

    async open() {
      await this.port.open(PORT_OPTIONS);
      if (this.closed) {
        await this.port.close();
        return;
      }
      this.reader = this.port.readable.getReader();
      this.writer = this.port.writable.getWriter();
      this.port.addEventListener("disconnect", this.onDisconnect);
      this.started = this.now();
      this.timer = this.interval(() => this.tick(), 400);
      this.tick();
      this.read();
    }

    async read() {
      try {
        while (!this.closed) {
          const { value, done } = await this.reader.read();
          if (done) break;
          this.lines.feed(value);
        }
      } catch {
        // A disconnected port can reject the read instead of reporting EOF.
      }
      if (!this.closed) this.close("lost");
    }

    send(line) {
      if (!this.writer || this.closed) return;
      if (this.writes >= 4) {
        this.close("lost");
        return;
      }
      this.writes += 1;
      this.writer.write(new TextEncoder().encode(line + "\n"))
        .catch(() => this.close("lost")).finally(() => { this.writes -= 1; });
    }

    tick() {
      const time = this.now();
      if (this.closed) return;
      if (!this.ready) {
        if (time - this.started > AUTH_WAIT_MS) this.close("unrecognized");
        else this.send(`HELLO ${this.epoch}`);
        return;
      }
      const target = this.armTarget;
      if (time - this.lastAck > WATCHDOG_MS || (target?.deadline && time > target.deadline)) {
        this.close("lost");
        return;
      }
      this.pingSeq += 1;
      this.send(`KEEP ${this.epoch} ${this.pingSeq}`);
    }

    onLine(line) {
      if (this.closed) return;
      const event = parsed(line, this.epoch);
      if (!event) return;
      if (event.type === "ready" && !this.ready) {
        this.ready = true;
        this.lastAck = this.now();
        this.status("ready");
        this.tick();
      } else if (this.ready && event.type === "pong" && this.ackSeq < event.seq && event.seq <= this.pingSeq) {
        this.ackSeq = event.seq;
        this.lastAck = this.now();
      } else if (this.ready && this.armTarget && this.armTarget.ticket === event.ticket) {
        this.targetEvent(event);
      }
    }

    targetEvent(event) {
      const target = this.armTarget;
      if (event.type === "target") {
        if (target.ack || target.id !== event.id || target.window !== event.window) return;
        target.ack = true;
        target.deadline = target.window ? this.now() + target.window + TARGET_WAIT_MS : 0;
      } else {
        if (!target.ack || (event.seq !== undefined && event.seq <= this.frameSeq)) return;
        if (event.type === "wrong") {
          if (target.id === event.id) return;
        } else if (target.id !== event.id || (event.type === "miss" && !target.window)) return;
        if (event.ms !== undefined && target.window && event.ms >= target.window) return;
        if (event.seq !== undefined) this.frameSeq = event.seq;
        if (event.type !== "wrong") this.armTarget = null;
      }
      this.event(event);
    }

    arm({ ticket, id, window }) {
      if (!this.ready || this.closed || this.armTarget || ticket <= this.lastTicket) {
        this.close("lost");
        return;
      }
      this.lastTicket = ticket;
      this.armTarget = { ticket, id, window, ack: false, deadline: this.now() + TARGET_WAIT_MS };
      this.send(`ARM ${this.epoch} ${ticket} ${id} ${window}`);
    }

    off() {
      this.armTarget = null;
      if (this.ready) this.send(`STOP ${this.epoch}`);
    }

    async close(reason = "closed") {
      if (this.closed) return;
      this.closed = true;
      this.ready = false;
      this.armTarget = null;
      this.port.removeEventListener("disconnect", this.onDisconnect);
      if (this.timer !== undefined) this.clear(this.timer);
      this.status(reason);
      // Do not let a disconnected serial object stall the UI on a faulty release.
      try { await this.reader?.cancel(); } catch { /* already disconnected */ }
      try { this.reader?.releaseLock(); } catch { /* already released */ }
      try { await this.writer?.abort(); } catch { /* already disconnected */ }
      try { this.writer?.releaseLock(); } catch { /* already released */ }
      try { await this.port.close(); } catch { /* OS already closed it */ }
    }
  }

  const api = { UsbStations, PORT_FILTERS, parsed, Lines };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.KidsReactionUsb = api;
}(globalThis));
