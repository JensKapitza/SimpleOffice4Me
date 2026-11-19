const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {resolveRevealTarget} = require('../desktop/electron/reveal');

function fsStub(files = {}) {
  return {
    statSync(target) {
      if (!(target in files)) {
        const error = new Error('missing');
        error.code = 'ENOENT';
        throw error;
      }
      return {isFile: () => files[target] === 'file'};
    },
  };
}

test('linux absolute file resolves', () => {
  const fs = fsStub({'/home/ada/Documents/a.txt': 'file'});
  assert.deepEqual(
    resolveRevealTarget('/home/ada/Documents/a.txt', {path, fs, platform: 'linux'}),
    {ok: true, path: '/home/ada/Documents/a.txt'},
  );
});

test('macOS absolute file resolves', () => {
  const fs = fsStub({'/Users/ada/Documents/a.txt': 'file'});
  assert.deepEqual(
    resolveRevealTarget('/Users/ada/Documents/a.txt', {path, fs, platform: 'darwin'}),
    {ok: true, path: '/Users/ada/Documents/a.txt'},
  );
});

test('windows absolute file resolves, drive-relative does not', () => {
  const fs = fsStub({'C:\\Users\\ada\\a.txt': 'file'});
  assert.deepEqual(
    resolveRevealTarget('C:\\Users\\ada\\a.txt', {path, fs, platform: 'win32'}),
    {ok: true, path: 'C:\\Users\\ada\\a.txt'},
  );
  assert.deepEqual(
    resolveRevealTarget('C:ada\\a.txt', {path, fs, platform: 'win32'}),
    {ok: false, reason: 'relative'},
  );
});

test('relative paths are rejected on every platform', () => {
  for (const platform of ['linux', 'darwin', 'win32']) {
    assert.deepEqual(
      resolveRevealTarget('../outside.txt', {path, fs: fsStub(), platform}),
      {ok: false, reason: 'relative'},
    );
    assert.deepEqual(
      resolveRevealTarget('Documents/a.txt', {path, fs: fsStub(), platform}),
      {ok: false, reason: 'relative'},
    );
  }
});

test('missing files and directories are rejected', () => {
  const fs = fsStub({'/home/ada/folder': 'dir', '/home/ada/a.txt': 'file'});
  assert.deepEqual(
    resolveRevealTarget('/home/ada/gone.txt', {path, fs, platform: 'linux'}),
    {ok: false, reason: 'missing'},
  );
  assert.deepEqual(
    resolveRevealTarget('/home/ada/folder', {path, fs, platform: 'linux'}),
    {ok: false, reason: 'not_file'},
  );
});

test('malformed inputs are rejected without touching fs', () => {
  let calls = 0;
  const fs = {statSync() { calls += 1; throw new Error('unreachable'); }};
  for (const input of [null, 42, {}, '', '   ', 'a\0b', `/${'x'.repeat(4100)}`]) {
    const result = resolveRevealTarget(input, {path, fs, platform: 'linux'});
    assert.equal(result.ok, false);
  }
  assert.equal(calls, 0);
});
