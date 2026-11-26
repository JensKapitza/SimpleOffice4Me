const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));
const origin = 'https://simpleoffice.invalid';
function load(context, file) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', file), 'utf8'), context);
}
function soundFixture({delayedPlay = false, transport, document: inputDocument, autoEnd = false} = {}) {
  const events = [], players = [], requests = [], revoked = [], hidden = {}, pageEvents = {};
  let blob = 0;
  class Audio {
    constructor(url) {
      this.url = url.startsWith(origin) ? url.slice(origin.length) : url;
      this.listeners = {};
      players.push(this);
      events.push(`create:${this.url}`);
    }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    emit(type) { (this.listeners[type] || []).forEach(fn => fn()); }
    end() { this.emit('ended'); }
    fail() { this.emit('error'); }
    play() {
      events.push(`play:${this.url}`);
      if (delayedPlay) return new Promise((_, reject) => { this.rejectPlay = reject; });
      if (autoEnd) queueMicrotask(() => { this.emit('playing'); this.end(); });
      return Promise.resolve();
    }
    pause() { events.push(`pause:${this.url}`); this.emit('pause'); }
  }
  const document = inputDocument ?? {
    hidden: false,
    querySelector(selector) { return selector.includes('csrf-token') ? {content: 'fixture-csrf'} : null; },
    addEventListener(type, listener) { hidden[type] = listener; },
  };
  const window = {
    Audio, location: new URL(origin + '/kids/lernen/'), AbortController,
    URL: {createObjectURL: () => `blob:fixture-${++blob}`, revokeObjectURL: value => revoked.push(value)},
    addEventListener(type, listener) { pageEvents[type] = listener; },
    CustomEvent: class { constructor(type, {detail}) { this.type = type; this.detail = detail; } },
    dispatchEvent(event) { pageEvents[event.type]?.(event); },
    fetch(url, options) {
      requests.push({url, options, payload: JSON.parse(options.body)});
      return transport ? transport(url, options) : Promise.resolve(wav());
    },
  };
  const context = vm.createContext({window, document, URL});
  load(context, 'kids_piper_voice.js');
  return {context, window, document, events, players, requests, revoked, hidden, pageEvents};
}
function wav() { return {ok: true, headers:{get: () => 'audio/wav'}, blob: async () => ({size: 16})}; }
module.exports = {load, soundFixture, tick, origin, wav};
