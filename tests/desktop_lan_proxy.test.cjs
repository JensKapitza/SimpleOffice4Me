'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const {createLanProxy, cleanHeaders, isPrivateClient} = require('../desktop/electron/lan_proxy');

async function freePort() {
  const socket = net.createServer();
  await new Promise((resolve) => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  return port;
}
async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

test('LAN proxy forwards method, path and body to fixed loopback backend', async () => {
  const backend = http.createServer((req, res) => {
    assert.equal(req.method, 'POST');
    assert.equal(req.url, '/screen/api/join?foo=1');
    assert.equal(req.headers['x-forwarded-proto'], 'http');
    assert.equal(req.headers['x-forwarded-host'], `127.0.0.1:${proxyPort}`);
    let body = '';
    req.setEncoding('utf8');
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      res.writeHead(201, {'content-type': 'text/plain'});
      res.end(body);
    });
  });
  await new Promise((resolve) => backend.listen(0, '127.0.0.1', resolve));
  const proxyPort = await freePort();
  const proxy = await createLanProxy({host: '127.0.0.1', port: proxyPort, backendPort: backend.address().port});
  try {
    const response = await fetch('http://127.0.0.1:' + proxyPort + '/screen/api/join?foo=1', {
      method: 'POST', headers: {'x-forwarded-host': 'attacker.example'}, body: 'payload'
    });
    assert.equal(response.status, 201);
    assert.equal(await response.text(), 'payload');
  } finally {
    await close(proxy);
    await close(backend);
  }
});

test('proxy removes hop-by-hop headers and untrusted forwarded headers', () => {
  const result = cleanHeaders({
    connection: 'close', 'x-forwarded-host': 'evil', 'proxy-authorization': 'secret',
    authorization: 'token', cookie: 'session=abc'
  });
  assert.deepEqual(result, {authorization: 'token', cookie: 'session=abc'});
});

test('LAN restriction rejects public addresses and allows private client networks', () => {
  for (const value of ['127.0.0.1', '192.168.1.2', '10.0.0.5', '172.16.0.1',
    '172.31.255.254', '169.254.2.3', '::1', '::ffff:192.168.1.3']) {
    assert.equal(isPrivateClient(value), true, value);
  }
  for (const value of ['8.8.8.8', '1.1.1.1', '172.32.0.1', '192.167.0.1',
    '0.0.0.0', '', 'example.org', '2001:4860:4860::8888']) {
    assert.equal(isPrivateClient(value), false, value);
  }
});
