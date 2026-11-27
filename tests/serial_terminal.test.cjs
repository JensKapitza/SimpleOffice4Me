const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const code = fs.readFileSync("static/js/serial-terminal.js", "utf8");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function fixture({local = "1", secure = true, host = "localhost", framed = false} = {}) {
  const nodes = new Map(), received = [], errors = [], downloads = [];
  function node(name) {
    if (!nodes.has(name)) {
      nodes.set(name, {value:"", textContent:"", scrollTop:0, scrollHeight:200, clientHeight:180,
        events:{}, addEventListener(evt, cb) { this.events[evt] = cb; }});
    }
    return nodes.get(name);
  }
  for (const [name,value] of Object.entries({baud:"9600",bits:"8",stopbits:"1",parity:"none",
    flow:"none",format:"text",ending:"crlf",channel:"/datalogger/channels/demo/samples"})) node(name).value = value;
  let reader, asks = 0, closed = 0, options, confirm = false;
  const port = {
    readable: new ReadableStream({start: (controller) => { reader = controller; }}),
    writable: new WritableStream({write: (bytes) => { received.push(Buffer.from(bytes)); }}),
    async open(value) { options = value; },
    async close() { closed += 1; assert.equal(this.readable.locked, false); assert.equal(this.writable.locked, false); },
  };
  const serial = {requestPort: async () => { asks += 1; return port; }, addEventListener(evt, cb) { this[evt] = cb; }};
  const win = {isSecureContext:secure, location:{hostname:host}, requestAnimationFrame: (fn) => setImmediate(fn), setTimeout: () => {},
    confirm: () => confirm, events:{}, addEventListener(evt, cb) { this.events[evt] = cb; }};
  win.top = framed ? {} : win;
  const root = {dataset:{local}, querySelector(selector) { return node(selector.slice(6,-1)); }};
  const doc = {querySelector:() => root, createElement:() => ({click() { errors.push(this.download); }})};
  vm.runInNewContext(code, {document:doc, window:win, navigator:{serial}, TextEncoder,TextDecoder,Uint8Array,
    Blob, btoa, URL:{createObjectURL(blob) {downloads.push(blob); return "blob:test";}, revokeObjectURL() {}}});
  return {node, received, errors, downloads, createText:(str) => reader.enqueue(new TextEncoder().encode(str)),
    put:(bytes) => reader.enqueue(bytes), asks:() => asks, closed:() => closed, options:() => options, serial, win,
    confirmClear:() => { confirm = true; }, port};
}

test("explicit serial chooser, UTF-8 and hex TX, streaming RX, selected channel, exact capture bytes", async () => {
  const f = fixture();
  assert.equal(f.asks(), 0);
  await f.node("connect").events.click();
  assert.equal(f.asks(), 1);
  assert.equal(f.options().baudRate, 9600);
  f.node("capture").events.click();
  f.createText("SO4M,2");
  f.createText("1.5\r\n<img src=x>\n");
  f.put(Uint8Array.of(0, 255));
  await tick();
  await tick();
  assert.equal(f.node("value").textContent, "21.5");
  assert.match(f.node("output").value, /<img src=x>/);
  f.node("sample-form").events.submit({preventDefault(){assert.fail("valid sample");}});
  assert.equal(f.node("sample").value, "21.5");
  assert.equal(f.node("sample-form").action, "/datalogger/channels/demo/samples");
  f.node("input").value = "Test";
  f.node("send-form").events.submit({preventDefault(){}});
  await tick();
  f.node("input").value = "00 FF";
  f.node("format").value = "hex";
  f.node("send-form").events.submit({preventDefault(){}});
  await tick();
  assert.deepEqual(f.received, [Buffer.from("Test\r\n"), Buffer.from([0, 255])]);
  f.node("capture").events.click();
  f.node("export").events.click();
  const capture = (await f.downloads[0].text()).trim().split("\n").map(JSON.parse);
  assert.equal(capture.length, 5);
  assert.deepEqual(Buffer.from(capture[2].base64, "base64"), Buffer.from([0,255]));
  assert.equal(capture[4].direction, "tx");
  assert.equal(f.node("export").disabled, false);
  f.node("clear").events.click();
  assert.equal(f.node("export").disabled, false);
  f.confirmClear();
  f.node("clear").events.click();
  assert.equal(f.node("export").disabled, true);
  f.node("disconnect").events.click();
  await tick();
  assert.equal(f.closed(), 1);
});

test("invalid hex and malformed or non-finite sensor data never enable writes", async () => {
  const f = fixture();
  await f.node("connect").events.click();
  f.createText("SO4M,1e999\n" + "x".repeat(90) + "SO4M,9\n");
  f.node("input").value = "0G";
  f.node("format").value = "hex";
  f.node("send-form").events.submit({preventDefault(){}});
  await tick();
  assert.equal(f.node("save").disabled, true);
  assert.equal(f.received.length, 0);
  f.win.events.pagehide();
  await tick();
  assert.equal(f.closed(), 1);
});

test("cap the capture while leaving port connected; disconnect releases stream", async () => {
  const f = fixture();
  await f.node("connect").events.click();
  f.node("capture").events.click();
  f.put(new Uint8Array(4 * 1024 * 1024 + 1));
  await tick();
  assert.match(f.node("capture-status").textContent, /grenze/);
  assert.equal(f.node("export").disabled, true);
  assert.equal(f.closed(), 0);
  f.serial.disconnect({target:f.port});
  await tick();
  assert.equal(f.closed(), 1);
});

test("remote or insecure context cannot access the port chooser", () => {
  for (const f of [fixture({local:"0"}),fixture({secure:false}),fixture({host:"office.test"}),fixture({framed:true})]) {
    assert.equal(f.node("connect").events.click, undefined);
    assert.equal(f.asks(), 0);
  }
});
