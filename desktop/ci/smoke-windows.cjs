'use strict';

const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {setTimeout: delay} = require('node:timers/promises');
const {Inspector} = require('./inspector.cjs');

const fatalOutput = /SIMPLEOFFICE_SMOKE_UNHANDLED|Cannot find module|MODULE_NOT_FOUND|ERR_MODULE_NOT_FOUND|A JavaScript error occurred in the main process|UnhandledPromiseRejection|uncaught exception|Traceback \(most recent call last\)|Python backend start failed|LAN proxy failed to start/i;

function installFaultObservers(state) {
  const record = (error) => {
    const detail = String(error?.stack || error);
    state.errors.push(detail);
    // Remains observable after the inspector detaches, including during quit.
    console.error('SIMPLEOFFICE_SMOKE_UNHANDLED', detail);
  };
  process.on('uncaughtExceptionMonitor', record);
  process.on('unhandledRejection', record);
}

function prepareProfile(root) {
  for (const name of ['appData', 'localAppData', 'temp', 'userData', 'sessionData', 'documents']) {
    fs.mkdirSync(path.join(root, name));
  }
  // Settings can override the error-reporting environment variable.
  const metadata = path.join(root, 'documents', 'SimpleOffice4Me', '.simpleoffice-meta');
  fs.mkdirSync(metadata, {recursive: true});
  fs.writeFileSync(path.join(metadata, 'settings.json'), JSON.stringify({
    integrations: {error_reporting_enabled: false}, server: {host: '127.0.0.1'}
  }));
}

function smokeEnvironment(root, inherited = process.env) {
  const env = Object.fromEntries(Object.entries(inherited).filter(([key]) =>
    !/^(SIMPLEOFFICE_|PYTHON|ELECTRON_|NODE_OPTIONS$|NODE_PATH$)/i.test(key)));
  return {...env,
    APPDATA: path.join(root, 'appData'), LOCALAPPDATA: path.join(root, 'localAppData'),
    HOME: root, USERPROFILE: root, TMP: path.join(root, 'temp'), TEMP: path.join(root, 'temp'),
    SIMPLEOFFICE_ERROR_REPORTING: '0', SIMPLEOFFICE_BACKGROUND_INDEX: '0',
    SIMPLEOFFICE_OSM_INDEX: '0', SIMPLEOFFICE_DATALOGGER: '0',
    SIMPLEOFFICE_LOG_STDERR_ONLY: '1', PYTHONHOME: '', PYTHONPATH: ''};
}

function selectProcesses(snapshot, directory, tracked) {
  const prefix = directory.toLowerCase() + path.sep;
  const key = p => `${p.ProcessId}:${p.CreationDate}`;
  const known = new Set(tracked.map(key));
  const selected = snapshot.filter(p => p.ExecutablePath?.toLowerCase().startsWith(prefix) || known.has(key(p)));
  for (let count = -1; count !== selected.length;) {
    count = selected.length;
    const parents = new Map(selected.map(p => [p.ProcessId, p]));
    for (const process of snapshot) {
      const parent = parents.get(process.ParentProcessId);
      if (parent && process.CreationDate >= parent.CreationDate && !selected.includes(process)) selected.push(process);
    }
  }
  return selected;
}

function processTracker(directory) {
  const tracked = new Map();
  return () => {
    const source = `$items = @(Get-CimInstance Win32_Process -ErrorAction Stop |
      Select-Object ProcessId, ParentProcessId, ExecutablePath, @{Name='CreationDate'; Expression={
        if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }
      }})
      ConvertTo-Json -InputObject $items -Compress`;
    const snapshot = JSON.parse(execFileSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command', source], {
      encoding: 'utf8', timeout: 15000
    }).trim());
    const selected = selectProcesses(snapshot, directory, [...tracked.values()]);
    for (const process of selected) tracked.set(`${process.ProcessId}:${process.CreationDate}`, process);
    return selected;
  };
}

function assertPage(status, contentType, content) {
  assert.equal(status, 200, 'Backend must serve HTTP 200');
  assert.match(contentType, /charset=utf-8/i, 'Backend must serve UTF-8');
  assert.match(content, /SimpleOffice4Me/, 'Backend must serve the application');
}

function assertRuntime(info, executable, root) {
  assert.equal(info.packaged, true, 'Development Electron is not a packaged runtime');
  assert.equal(path.resolve(info.executable), executable);
  assert.equal(path.resolve(info.appPath), path.join(path.dirname(executable), 'resources', 'app.asar'));
  for (const name of ['userData', 'sessionData', 'documents']) {
    assert.equal(path.resolve(info.paths[name]), path.join(root, name), `Unisolated Electron ${name}`);
  }
  assert.deepEqual(info.errors, [], 'Unhandled main-process errors');
}

async function waitUntil(check, timeout, description) {
  const end = Date.now() + timeout;
  do {
    const value = await check();
    if (value) return value;
    await delay(250);
  } while (Date.now() < end);
  throw new Error(`Timeout: ${description}`);
}

async function listenerClosed(url) {
  const endpoint = new URL(url);
  assert.equal(endpoint.hostname, '127.0.0.1');
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({host: '127.0.0.1', port: Number(endpoint.port)});
    socket.once('connect', () => { socket.destroy(); resolve(false); });
    socket.once('error', error => error.code === 'ECONNREFUSED' ? resolve(true) : reject(error));
    socket.setTimeout(1000, () => { socket.destroy(); reject(new Error('Backend listener probe timed out')); });
  });
}

async function main() {
  if (process.platform !== 'win32') throw new Error('Windows runtime required; no static fallback');
  const executable = path.resolve(process.argv[2] || 'desktop/electron/dist/win-unpacked/SimpleOffice4Me.exe');
  const output = path.resolve(process.argv[3] || 'artifacts/windows-start-smoke');
  fs.mkdirSync(output, {recursive: true});
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'simpleoffice-electron-smoke-'));
  const report = {runtime_executed: false, passed: false, stage: 'preflight', forced_cleanup: false};
  const packagedProcesses = processTracker(path.dirname(executable));
  let child, inspector, outputText = '', backendUrl;
  let exit = null;
  let closed = false;
  let ownsPackage = false;
  try {
    prepareProfile(root);
    for (const file of [executable, path.join(path.dirname(executable), 'resources', 'app.asar'),
      path.join(path.dirname(executable), 'resources', 'backend', 'simpleoffice-python.exe')]) {
      assert.ok(fs.statSync(file).isFile(), `Missing packaged file: ${file}`);
    }
    let capabilities;
    try {
      capabilities = execFileSync('pwsh', ['-NoProfile', '-File', path.join(__dirname, 'windows-capabilities.ps1')],
        {encoding: 'utf8', timeout: 20000});
    } catch (error) {
      fs.writeFileSync(path.join(output, 'runner-preflight.log'), String(error.stdout || '') + String(error.stderr || error));
      throw error;
    }
    report.runner = JSON.parse(capabilities);
    fs.writeFileSync(path.join(output, 'runner.json'), capabilities);
    assert.deepEqual(packagedProcesses(), [], 'Package already running');
    ownsPackage = true;
    report.stage = 'launch';
    child = spawn(executable, ['--inspect-brk=127.0.0.1:0', '--enable-logging=stderr',
      `--user-data-dir=${path.join(root, 'userData')}`], {
      cwd: root, env: smokeEnvironment(root), stdio: ['ignore', 'pipe', 'pipe']
    });
    report.pid = child.pid;
    let spawnError;
    child.on('spawn', () => { report.runtime_executed = true; });
    child.on('error', (error) => { spawnError = error; });
    child.on('exit', (code, signal) => { exit = {code, signal}; });
    child.on('close', () => { closed = true; });
    for (const stream of [child.stdout, child.stderr]) stream.on('data', (chunk) => { outputText += chunk.toString(); });
    const debuggerUrl = await waitUntil(() => {
      if (spawnError) throw spawnError;
      if (exit) throw new Error(`Electron exited before inspector: ${JSON.stringify(exit)}`);
      return outputText.match(/ws:\/\/127\.0\.0\.1:\d+\/[\w-]+/)?.[0];
    }, 20000, 'main-process inspector');
    inspector = await Inspector.connect(debuggerUrl);
    await inspector.send('Runtime.enable');
    await inspector.send('Debugger.enable');
    // Break inside the real ASAR entry, where CommonJS require is available.
    await inspector.send('Debugger.setBreakpointByUrl', {urlRegex: String.raw`app\.asar[/\\]main\.js$`, lineNumber: 0});
    await inspector.send('Runtime.runIfWaitingForDebugger');
    let frame;
    for (let attempt = 0; attempt < 10; attempt++) {
      const paused = await inspector.waitFor('Debugger.paused');
      const candidate = paused.callFrames[0];
      const scripts = inspector.events.filter((event) => event.method === 'Debugger.scriptParsed');
      const url = candidate.url || scripts.find((event) => event.params.scriptId === candidate.location.scriptId)?.params.url || '';
      if (/app\.asar[/\\]main\.js$/.test(url)) { frame = candidate; break; }
      await inspector.send('Debugger.resume');
    }
    assert.ok(frame, 'Did not pause before packaged main.js');
    report.stage = 'isolate-before-main';
    await inspector.evaluate(`(() => {
      const electron = require('electron');
      if (electron.app.isReady()) throw new Error('Isolation occurred after Electron ready');
      globalThis.__simpleofficeSmoke = {electron, errors: []};
      const root = ${JSON.stringify(root)};
      for (const name of ['userData', 'sessionData', 'documents']) {
        electron.app.setPath(name, require('node:path').join(root, name));
      }
      (${installFaultObservers.toString()})(__simpleofficeSmoke);
      return true;
    })()`, frame.callFrameId);
    await inspector.send('Debugger.resume');
    const snapshot = `(() => {
      const {electron, errors} = __simpleofficeSmoke;
      return {packaged: electron.app.isPackaged, executable: process.execPath,
        appPath: electron.app.getAppPath(), errors,
        paths: Object.fromEntries(['userData', 'sessionData', 'documents'].map(n => [n, electron.app.getPath(n)])),
        windows: electron.app.isReady() ? electron.BrowserWindow.getAllWindows().map(w =>
          ({url: w.webContents.getURL(), loading: w.webContents.isLoading()})) : []};
    })()`;
    report.stage = 'backend-and-window';
    const info = await waitUntil(async () => {
      if (exit) throw new Error(`Electron exited during startup: ${JSON.stringify(exit)}`);
      assert.ok(!fatalOutput.test(outputText), 'Fatal startup output; see electron.log');
      report.processes = packagedProcesses();
      const state = await inspector.evaluate(snapshot);
      assertRuntime(state, executable, root);
      const window = state.windows.find(w => /^http:\/\/127\.0\.0\.1:\d+\//.test(w.url) && !w.loading);
      if (!window) return false;
      backendUrl = new URL(window.url).origin + '/';
      const response = await fetch(backendUrl, {redirect: 'manual', signal: AbortSignal.timeout(3000)});
      assertPage(response.status, response.headers.get('content-type') || '', await response.text());
      return state;
    }, 90000, 'packaged backend HTTP and loaded Electron window');
    report.application = info;
    report.backend_url = backendUrl;
    report.processes = packagedProcesses();
    assert.ok(report.processes.some(p => p.ProcessId === child.pid), 'Main process identity missing');
    const backendPath = path.join(path.dirname(executable), 'resources', 'backend', 'simpleoffice-python.exe');
    assert.ok(report.processes.some(p => p.ParentProcessId === child.pid &&
      p.ExecutablePath.toLowerCase() === backendPath.toLowerCase()), 'Embedded backend child not running');
    // Observe a short stable interval, so asynchronous startup errors cannot immediately pass.
    await delay(2000);
    assertRuntime(await inspector.evaluate(snapshot), executable, root);
    assert.ok(!inspector.events.some(e => e.method === 'Runtime.exceptionThrown'), 'Inspector reported an uncaught exception');
    report.stage = 'controlled-quit';
    await inspector.evaluate('setTimeout(() => __simpleofficeSmoke.electron.app.quit(), 100); true');
    inspector.close(); // A connected debugger can keep Node alive during shutdown.
    inspector = null;
    await waitUntil(() => exit, 20000, 'normal Electron exit');
    await waitUntil(() => closed, 5000, 'Electron stdout/stderr drained');
    assert.equal(exit.code, 0, `Unexpected Electron exit: ${JSON.stringify(exit)}`);
    await waitUntil(() => packagedProcesses().length === 0, 15000, 'all packaged descendants stopped');
    assert.equal(await listenerClosed(backendUrl), true, 'Backend listener survived Electron quit');
    assert.ok(!fatalOutput.test(outputText), 'Fatal runtime/shutdown output; see electron.log');
    report.stage = 'complete';
    report.passed = true;
  } catch (error) {
    report.error = error.stack || String(error);
  } finally {
    inspector?.close();
    try {
      const remaining = ownsPackage ? packagedProcesses() : [];
      report.remaining_before_cleanup = remaining;
      if (remaining.length) {
        report.forced_cleanup = true;
        report.passed = false;
        for (const process of remaining) {
          // Recheck identity immediately before killing to avoid unrelated/reused PIDs.
          const stillExists = () => packagedProcesses().some(p =>
            p.ProcessId === process.ProcessId && p.CreationDate === process.CreationDate);
          if (!stillExists()) continue;
          try {
            execFileSync('taskkill.exe', ['/PID', String(process.ProcessId), '/T', '/F'], {timeout: 10000});
          } catch (error) {
            if (stillExists()) throw error;
          }
        }
      }
      if (ownsPackage) await waitUntil(() => packagedProcesses().length === 0, 10000, 'failure cleanup');
      if (child && !closed) await waitUntil(() => closed, 5000, 'cleanup stdout/stderr drained');
      fs.rmSync(root, {recursive: true, force: true, maxRetries: 10, retryDelay: 500});
      report.cleanup_complete = true;
    } catch (error) {
      report.passed = false;
      report.cleanup_error = error.stack || String(error);
    }
    report.exit = exit;
    fs.writeFileSync(path.join(output, 'electron.log'), outputText);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n');
  }
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
}

module.exports = {assertPage, assertRuntime, fatalOutput, installFaultObservers, listenerClosed, prepareProfile, selectProcesses, smokeEnvironment, waitUntil};
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
