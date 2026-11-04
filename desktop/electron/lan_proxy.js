'use strict';

// LAN reverse proxy for the embedded desktop backend. Deliberately opt-in:
// the persisted server bind must explicitly differ from loopback.
const http = require('node:http');

const HOP_HEADERS = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'proxy-connection'
]);

function cleanHeaders(headers) {
  const result = {};
  for (const [key, value] of Object.entries(headers)) {
    if (!HOP_HEADERS.has(key.toLowerCase()) && !key.toLowerCase().startsWith('x-forwarded-')) {
      result[key] = value;
    }
  }
  return result;
}

function createLanProxy({host, port, backendPort, logger = console}) {
  if (!Number.isInteger(port) || port < 1 || port > 65535 ||
      !Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65535) {
    throw new Error('Ungültiger Proxy-Port');
  }
  // The proxy must never target an address supplied by the HTTP client.
  const server = http.createServer((req, res) => {
    const headers = cleanHeaders(req.headers);
    headers.host = req.headers.host || `${host}:${port}`;
    headers['x-forwarded-proto'] = 'http';
    headers['x-forwarded-for'] = req.socket.remoteAddress || '';
    const upstream = http.request({
      host: '127.0.0.1', port: backendPort, method: req.method,
      path: req.url, headers, timeout: 120000
    }, (response) => {
      if (res.headersSent) return;
      res.writeHead(response.statusCode || 502, cleanHeaders(response.headers));
      response.pipe(res);
    });
    upstream.on('timeout', () => upstream.destroy(new Error('Backend timeout')));
    upstream.on('error', (error) => {
      logger.warn('LAN proxy backend failure:', error.code || error.name);
      if (!res.headersSent) {
        res.writeHead(502, {'content-type': 'text/plain; charset=utf-8'});
        res.end('SimpleOffice backend unavailable');
      } else {
        res.destroy();
      }
    });
    req.on('aborted', () => upstream.destroy());
    req.pipe(upstream);
  });
  server.on('upgrade', (_request, socket) => socket.destroy());
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.removeListener('error', reject);
      resolve(server);
    });
  });
}

module.exports = {createLanProxy, cleanHeaders};
