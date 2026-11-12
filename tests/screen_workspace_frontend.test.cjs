const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const frameHandlers = {}, documentHandlers = {}, innerHandlers = {}, events = [];
  const origin = 'https://office.example';
  class Input {
    constructor(name, type = 'text') {
      this.name = name; this.id = ''; this.type = type; this.tagName = 'INPUT';
      this.isConnected = true; this.labels = [{textContent: name}]; this.maxLength = -1;
      this.validity = {valid: true}; this._value = 'local value'; this._checked = false;
    }
    get value() { return this._value; } set value(value) { this._value = value; }
    get checked() { return this._checked; } set checked(value) { this._checked = value; }
    matches() { return false; } getClientRects() { return this.invisible ? [] : [{}]; }
    closest() { return null; } getAttribute() { return null; }
    dispatchEvent(event) { events.push(event.type); }
  }
  const realm = {HTMLInputElement: Input, HTMLTextAreaElement: Input, HTMLSelectElement: Input,
    Event: class {constructor(type) {this.type = type;}}, getComputedStyle: () => ({visibility: 'visible', display: 'block'})};
  const inner = {URL: origin + '/documents/', defaultView: realm,
    addEventListener(type, callback) { innerHandlers[type] = callback; }, querySelectorAll: () => fields};
  const fields = [new Input('Title'), new Input('Password', 'password'), new Input('api_token'), new Input('Remember', 'checkbox')];
  fields.forEach(field => {field.ownerDocument = inner;});
  const form = (action, target = null) => ({action, target,
    getAttribute: () => target, setAttribute(name, value) {this[name] = value;}, removeAttribute(name) {delete this[name];}});
  const forms = [form(origin + '/quick-search'), form(origin + '/settings/language', '_self'), form(origin + '/auth/logout')];
  const section = {hidden: true};
  const frame = {dataset: {startUrl: '/documents/'}, contentDocument: inner,
    contentWindow: {location: {origin, pathname: '/documents/'}},
    getAttribute(name) {return this[name] || null;}, addEventListener(type, callback) {frameHandlers[type] = callback;}};
  const document = {querySelectorAll: () => forms, getElementById: id => id === 'screen-workspace-section' ? section : frame,
    addEventListener(type, callback) {documentHandlers[type] = callback;}};
  const window = {location: {origin, href: origin + '/screen'}, dispatchEvent(event) {events.push(event.type);},
    open(...args) {events.push(args);}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/screen_workspace.js'), 'utf8'),
    {window, document, URL, WeakMap, CustomEvent: class {constructor(type) {this.type = type;}}});
  return {api: window.SimpleOfficeScreenWorkspace, section, frame, frameHandlers, documentHandlers, innerHandlers, fields, events, forms};
}

test('Workspace remains loaded after sharing stops and resumes without losing form data', () => {
  const f = fixture();
  assert.equal(f.api.fields().fields.length, 0);
  f.api.start(); f.frameHandlers.load();
  assert.equal(f.section.hidden, false);
  assert.equal(f.frame.src, '/documents/');
  const state = f.api.fields();
  assert.equal(state.fields.length, 2);
  assert.equal(Object.hasOwn(state.fields[0], 'value'), false);
  f.api.apply({epoch: state.epoch, field_id: state.fields[0].id, value: 'edited'});
  assert.equal(f.fields[0].value, 'edited');
  assert.equal(f.events.includes('input'), false);
  assert.equal(f.events.includes('change'), false);  // No auto-submit listeners invoked.
  f.api.stop();
  assert.equal(f.api.fields().fields.length, 0);
  assert.equal(f.fields[0].value, 'edited');
  assert.equal(f.section.hidden, false);
  f.api.start();
  assert.equal(f.fields[0].value, 'edited');
});

test('Stale pages, sensitive pages and fields, disabled fields and bad values reject edits', () => {
  const f = fixture(); f.api.start(); f.frameHandlers.load();
  const state = f.api.fields();
  assert.throws(() => f.api.apply({epoch: state.epoch, field_id: state.fields[1].id, value: 'true'}), /Ungültige/);
  f.fields[0].disabled = true;
  assert.throws(() => f.api.apply({epoch: state.epoch, field_id: state.fields[0].id, value: 'x'}), /nicht zur Eingabe/);
  f.fields[0].disabled = false;
  f.fields[0].maxLength = 2;
  assert.throws(() => f.api.apply({epoch: state.epoch, field_id: state.fields[0].id, value: 'long'}), /zu lang/);
  f.frameHandlers.load();
  assert.throws(() => f.api.apply({epoch: state.epoch, field_id: state.fields[0].id, value: 'x'}), /gewechselt/);
  f.frame.contentWindow.location.pathname = '/admin/users';
  assert.equal(f.api.fields().fields.length, 0);
});

test('Navigation moves within the workspace and external frame links open separately', () => {
  const f = fixture(); f.api.start(); f.frameHandlers.load();
  const event = href => ({target: {closest: () => ({href, hasAttribute: () => false})}, preventDefault() {this.prevented = true;}});
  const local = event('https://office.example/documents/contacts');
  f.documentHandlers.click(local);
  assert.equal(local.prevented, true);
  assert.equal(f.frame.src, 'https://office.example/documents/contacts');
  const external = event('https://example.org/');
  f.innerHandlers.click(external);
  assert.equal(external.prevented, true);
  assert.deepEqual(f.events.at(-1), ['https://example.org/', '_blank', 'noopener,noreferrer']);
  const form = {action: 'https://office.example/documents/', target: '_top'};
  f.innerHandlers.submit({target: form});
  assert.equal(form.target, '_self');
});


test('Dropdown and same-document anchor controls do not navigate the application frame', () => {
  const f = fixture(); f.api.start(); f.frameHandlers.load();
  const previous = f.frame.src;
  const link = {href: 'https://office.example/documents/#menu', getAttribute: () => '#menu', hasAttribute: () => false};
  f.innerHandlers.click({target: {closest: () => link}, preventDefault() {throw new Error('anchor intercepted');}});
  assert.equal(f.frame.src, previous);
});


test('Outer search and native language submission target the workspace and restore on stop', () => {
  const f = fixture(); f.api.start(); f.frameHandlers.load();
  assert.equal(f.frame.name, 'screen-application-workspace');
  assert.equal(f.forms[0].target, f.frame.name);
  assert.equal(f.forms[1].target, f.frame.name); // Native form.submit() honors this target.
  assert.equal(f.forms[2].target, null); // Logout deliberately ends the application session.
  const before = f.api.fields().epoch;
  f.documentHandlers.submit({target: f.forms[0]});
  assert.notEqual(f.api.fields().epoch, before);
  f.api.stop();
  assert.equal(Object.hasOwn(f.forms[0], 'target'), false);
  assert.equal(f.forms[1].target, '_self');
});
