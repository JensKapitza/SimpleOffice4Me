const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

// safePreviewUrl is a pure function inside the page IIFE; extract the exact
// shipped source and evaluate it standalone so the test covers the real code.
const source = readFileSync('static/js/capture_inbox.js', 'utf8');
const match = source.match(/const safePreviewUrl = \(value\) => \{[\s\S]*?\n  \};/);
assert.ok(match, 'safePreviewUrl must be extractable from shipped capture_inbox.js');
const safePreviewUrl = runInNewContext(`${match[0]}; safePreviewUrl;`, {});

test('relative, https, blob and image-data URLs pass', () => {
  assert.equal(safePreviewUrl('/inventory/capture/preview/1'), '/inventory/capture/preview/1');
  assert.equal(safePreviewUrl('https://office.example/pic.jpg'), 'https://office.example/pic.jpg');
  assert.equal(safePreviewUrl('blob:https://office.example/uuid'), 'blob:https://office.example/uuid');
  assert.equal(safePreviewUrl('data:image/png;base64,iVBOR'), 'data:image/png;base64,iVBOR');
});

test('plain http and dangerous schemes fail closed', () => {
  assert.equal(safePreviewUrl('http://office.example/pic.jpg'), '');
  assert.equal(safePreviewUrl('HTTP://office.example/pic.jpg'), '');
  assert.equal(safePreviewUrl('javascript:alert(1)'), '');
  assert.equal(safePreviewUrl('//evil.example/pic.jpg'), '');
  assert.equal(safePreviewUrl('data:text/html,<img src=x onerror=alert(1)>'), '');
  assert.equal(safePreviewUrl(''), '');
});
