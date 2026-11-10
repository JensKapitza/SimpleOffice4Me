'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {Inspector} = require('../desktop/ci/inspector.cjs');
const {assertPage, assertRuntime, fatalOutput, installFaultObservers, listenerClosed, prepareProfile, selectProcesses, smokeEnvironment, waitUntil} = require('../desktop/ci/smoke-windows.cjs');

test('Windows capability preflight avoids slow CIM process queries', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'desktop', 'ci', 'windows-capabilities.ps1'), 'utf8');
  assert.doesNotMatch(script, /Get-CimInstance|Win32_Process/);
  assert.match(script, /Get-Process -Id \\$PID -ErrorAction Stop/);
  assert.match(script, /ProcessQuery = \\(\\$self\.Id -eq \\$PID -and -not \\$self\.HasExited\\)/);
});

test('listener cleanup requires a refused TCP connection rather than a failed HTTP request', async (t) => {
  const server = net.createServer(socket => socket.end());
  t.after(() => server.close());
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  assert.equal(await listenerClosed(url), false);
  await new Promise(resolve => server.close(resolve));
  assert.equal(await listenerClosed(url), true);
  await assert.rejects(listenerClosed('http://example.com:80/'));
});

test('process tracking follows external descendants and retains orphans without confusing reused PIDs', () => {
  const pkg = path.resolve('package');
  const main = {ProcessId: 1, ParentProcessId: 0, CreationDate: 'start-1', ExecutablePath: path.join(pkg, 'app.exe')};
  const child = {ProcessId: 2, ParentProcessId: 1, CreationDate: 'start-2', ExecutablePath: '/outside/helper.exe'};
  const grandchild = {ProcessId: 3, ParentProcessId: 2, CreationDate: 'start-3', ExecutablePath: '/outside/worker.exe'};
  const unrelated = {ProcessId: 4, ParentProcessId: 0, CreationDate: 'start-4', ExecutablePath: pkg + '-other/app.exe'};
  const tracked = selectProcesses([grandchild, child, unrelated, main], pkg, []);
  assert.deepEqual(new Set(tracked.map(p => p.ProcessId)), new Set([1, 2, 3]));
  assert.deepEqual(selectProcesses([grandchild], pkg, tracked), [grandchild]);
  assert.deepEqual(selectProcesses([{...grandchild, CreationDate: 'new-process'}], pkg, tracked), []);
});

test('profile disables persisted reporting and isolates document storage', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smoke-profile-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  prepareProfile(root);
  const settings = JSON.parse(fs.readFileSync(path.join(root, 'documents', 'SimpleOffice4Me', '.simpleoffice-meta', 'settings.json')));
  assert.equal(settings.integrations.error_reporting_enabled, false);
  assert.equal(settings.server.host, '127.0.0.1');
});

test('isolated environment removes inherited injection and reporting configuration', () => {
  const env = smokeEnvironment('/isolated', {PATH: '/bin', SystemRoot: 'C:\\Windows',
    NODE_OPTIONS: '--require=untrusted', ELECTRON_RUN_AS_NODE: '1', PYTHONPATH: 'private',
    SIMPLEOFFICE_DOCUMENT_ROOT: '/real-data', SIMPLEOFFICE_ERROR_REPORT_URL: 'https://external.example'});
  assert.equal(env.PATH, '/bin');
  assert.equal(env.SystemRoot, 'C:\\Windows');
  assert.equal(env.NODE_OPTIONS, undefined);
  assert.equal(env.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(env.SIMPLEOFFICE_DOCUMENT_ROOT, undefined);
  assert.equal(env.SIMPLEOFFICE_ERROR_REPORT_URL, undefined);
  assert.equal(env.PYTHONPATH, '');
  assert.equal(env.SIMPLEOFFICE_ERROR_REPORTING, '0');
  assert.equal(env.APPDATA, path.join('/isolated', 'appData'));
});

test('HTTP readiness rejects error pages, wrong encoding and unrelated servers', () => {
  assertPage(200, 'text/html; charset=utf-8', '<title>SimpleOffice4Me</title>');
  for (const args of [[500, 'text/html; charset=utf-8', 'SimpleOffice4Me'],
    [302, 'text/html; charset=utf-8', 'SimpleOffice4Me'],
    [200, 'text/html; charset=windows-1252', 'SimpleOffice4Me'],
    [200, 'text/html; charset=utf-8', 'another app']]) {
    assert.throws(() => assertPage(...args));
  }
});

test('runtime identity rejects development mode, wrong ASAR, paths and unhandled errors', () => {
  const root = path.resolve('isolated');
  const executable = path.resolve('package', 'SimpleOffice4Me.exe');
  const info = {packaged: true, executable,
    appPath: path.join(path.dirname(executable), 'resources', 'app.asar'), errors: [],
    paths: Object.fromEntries(['userData', 'sessionData', 'documents'].map(n => [n, path.join(root, n)]))};
  assertRuntime(info, executable, root);
  for (const patch of [{packaged: false}, {appPath: path.resolve('main.js')},
    {errors: ['unhandled rejection']}, {paths: {...info.paths, documents: '/real-documents'}}]) {
    assert.throws(() => assertRuntime({...info, ...patch}, executable, root));
  }
});

test('module failures and Python startup tracebacks are fatal', () => {
  for (const text of ["Cannot find module './lan_proxy'", 'MODULE_NOT_FOUND',
    'UnhandledPromiseRejectionWarning', 'A JavaScript error occurred in the main process',
    'Traceback (most recent call last)', 'Python backend start failed']) {
    assert.match(text, fatalOutput);
  }
  assert.doesNotMatch('Debugger listening on ws://127.0.0.1:1234/abc', fatalOutput);
});

test('bounded polling fails rather than accepting a startup timeout or process leak', async () => {
  await assert.rejects(waitUntil(() => false, 1, 'backend still alive'), /Timeout: backend still alive/);
  await assert.rejects(waitUntil(() => { throw new Error('process query failed'); }, 1, 'cleanup'), /process query failed/);
});

test('real Node inspector pauses before entry, evaluates module scope, and detaches for exit', {timeout: 20000}, async (t) => {
  // This executes a real Node process, not Electron or a Windows package.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smoke-inspector-test-'));
  const entry = path.join(root, 'entry.cjs');
  const marker = path.join(root, 'started');
  fs.writeFileSync(entry, `require('node:fs').writeFileSync(${JSON.stringify(marker)}, globalThis.preMainValue);\nsetInterval(() => {}, 1000);\n`);
  const child = spawn(process.execPath, ['--inspect-brk=127.0.0.1:0', entry], {stdio: ['ignore', 'pipe', 'pipe']});
  let inspector, output = '', exit;
  child.stderr.on('data', chunk => { output += chunk.toString(); });
  child.on('exit', (code, signal) => { exit = {code, signal}; });
  t.after(async () => {
    inspector?.close();
    if (!exit) { child.kill(); await waitUntil(() => exit, 5000, 'fixture cleanup'); }
    fs.rmSync(root, {recursive: true, force: true});
  });
  const url = await waitUntil(() => output.match(/ws:\/\/127\.0\.0\.1:\d+\/[\w-]+/)?.[0], 5000, 'fixture inspector');
  inspector = await Inspector.connect(url);
  await inspector.send('Runtime.enable');
  await inspector.send('Debugger.enable');
  await inspector.send('Runtime.runIfWaitingForDebugger');
  const paused = await inspector.waitFor('Debugger.paused');
  assert.equal(fs.existsSync(marker), false, 'entry must not have run yet');
  const value = await inspector.evaluate("globalThis.preMainValue = 'isolated'; typeof require", paused.callFrames[0].callFrameId);
  assert.equal(value, 'function');
  await inspector.send('Debugger.resume');
  await waitUntil(() => fs.existsSync(marker), 5000, 'fixture entry');
  assert.equal(fs.readFileSync(marker, 'utf8'), 'isolated');
  await assert.rejects(inspector.evaluate("(() => { throw new Error('intentional smoke failure'); })()"), /intentional smoke failure/);
  await assert.rejects(inspector.send('Unknown.method'), /wasn't found/);
  await assert.rejects(inspector.waitFor('nonexistent', 10), /event timeout/);
  await inspector.evaluate('setTimeout(() => process.exit(0), 100); true');
  inspector.close();
  inspector = null;
  await waitUntil(() => exit, 5000, 'fixture normal exit');
  assert.equal(exit.code, 0);
});

test('real child startup module failure and late shutdown rejection remain fatal in logs', {timeout: 10000}, async () => {
  for (const code of ["require('./nonexistent-smoke-module')",
    "setTimeout(() => Promise.reject(new Error('late shutdown failure')), 50); setTimeout(() => process.exit(0), 100)"]) {
    const child = spawn(process.execPath, ['-e', `(${installFaultObservers.toString()})({errors: []}); ${code}`],
      {stdio: ['ignore', 'pipe', 'pipe']});
    let output = '';
    child.stderr.on('data', chunk => { output += chunk.toString(); });
    await new Promise((resolve, reject) => { child.once('close', resolve); child.once('error', reject); });
    assert.match(output, fatalOutput);
    assert.match(output, /SIMPLEOFFICE_SMOKE_UNHANDLED/);
  }
});
