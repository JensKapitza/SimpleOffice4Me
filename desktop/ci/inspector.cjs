'use strict';

// Minimal Node 24 Inspector transport; no browser/test framework dependency.
class Inspector {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    this.events = [];
    this.listeners = new Set();
    socket.addEventListener('message', ({data}) => {
      const message = JSON.parse(String(data));
      if (message.id) {
        const request = this.pending.get(message.id);
        if (!request) return;
        this.pending.delete(message.id);
        clearTimeout(request.timer);
        if (message.error) request.reject(new Error(JSON.stringify(message.error)));
        else request.resolve(message.result);
      } else {
        this.events.push(message);
        for (const listener of this.listeners) listener(message);
      }
    });
    socket.addEventListener('close', () => {
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error('Inspector disconnected'));
      }
      this.pending.clear();
    });
  }

  static async connect(url, timeout = 10000) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { socket.close(); reject(new Error('Inspector connection timeout')); }, timeout);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, {once: true});
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Inspector connection failed')); }, {once: true});
    });
    return new Inspector(socket);
  }

  send(method, params = {}, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Inspector timeout: ${method}`));
      }, timeout);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  async waitFor(method, timeout = 10000) {
    const take = () => {
      const index = this.events.findIndex((event) => event.method === method);
      return index < 0 ? null : this.events.splice(index, 1)[0].params;
    };
    const existing = take();
    if (existing) return existing;
    return new Promise((resolve, reject) => {
      const listener = () => {
        const event = take();
        if (!event) return;
        clearTimeout(timer);
        this.listeners.delete(listener);
        resolve(event);
      };
      const timer = setTimeout(() => {
        this.listeners.delete(listener);
        reject(new Error(`Inspector event timeout: ${method}`));
      }, timeout);
      this.listeners.add(listener);
    });
  }

  async evaluate(expression, callFrameId) {
    const result = await this.send(callFrameId ? 'Debugger.evaluateOnCallFrame' : 'Runtime.evaluate', {
      expression, returnByValue: true, ...(callFrameId ? {callFrameId} : {awaitPromise: true})
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }

  close() { this.socket.close(); }
}

module.exports = {Inspector};
