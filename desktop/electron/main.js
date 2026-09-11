'use strict';

const { app, BrowserWindow, shell, session } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

let mainWindow = null;
let backend = null;
let backendUrl = null;
let quitting = false;

function packagedErrorReportUrl() {
  try {
    const configPath = path.join(__dirname, 'build-config.json');
    const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const value = typeof parsed.errorReportUrl === 'string' ? parsed.errorReportUrl.trim() : '';
    if (!value) return '';
    if (!value.startsWith('https://')) {
      throw new Error('Packaged error report URL must use HTTPS');
    }
    return value;
  } catch (error) {
    if (error && error.code === 'ENOENT') return '';
    console.error('Invalid desktop build configuration:', error);
    return '';
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen({ host: '127.0.0.1', port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function packagedBackendPath() {
  const executable = process.platform === 'win32' ? 'simpleoffice-python.exe' : 'simpleoffice-python';
  return path.join(process.resourcesPath, 'backend', executable);
}

function developmentBackend() {
  const entry = path.resolve(__dirname, '..', 'python-setup', 'runtime_entry.py');
  const python = process.env.SIMPLEOFFICE_DESKTOP_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return { command: python, args: [entry] };
}

function backendCommand() {
  const packaged = packagedBackendPath();
  if (app.isPackaged) {
    if (!fs.existsSync(packaged)) {
      throw new Error(`Gebündeltes Python-Backend fehlt: ${packaged}`);
    }
    return { command: packaged, args: [] };
  }
  if (fs.existsSync(packaged)) {
    return { command: packaged, args: [] };
  }
  return developmentBackend();
}

async function startBackend() {
  const port = await freePort();
  const userData = app.getPath('userData');
  const dataDir = path.join(userData, 'backend');
  const documentRoot = path.join(app.getPath('documents'), 'SimpleOffice4Me');
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(documentRoot, { recursive: true });

  backendUrl = `http://127.0.0.1:${port}`;
  const launch = backendCommand();
  const errorReportUrl = (process.env.SIMPLEOFFICE_ERROR_REPORT_URL || packagedErrorReportUrl()).trim();
  if (errorReportUrl && !errorReportUrl.startsWith('https://')) {
    throw new Error('SIMPLEOFFICE_ERROR_REPORT_URL muss HTTPS verwenden.');
  }
  const env = {
    ...process.env,
    PYTHONUTF8: '1',
    SIMPLEOFFICE_DESKTOP: '1',
    SIMPLEOFFICE_DATA_DIR: dataDir,
    SIMPLEOFFICE_INSTANCE_DIR: path.join(dataDir, 'instance'),
    SIMPLEOFFICE_DOCUMENT_ROOT: documentRoot,
    SIMPLEOFFICE_HOST: '127.0.0.1',
    SIMPLEOFFICE_PORT: String(port),
    SIMPLEOFFICE_ERROR_REPORT_URL: errorReportUrl,
    SIMPLEOFFICE_BACKGROUND_INDEX: process.env.SIMPLEOFFICE_BACKGROUND_INDEX || '0',
    SIMPLEOFFICE_OSM_INDEX: process.env.SIMPLEOFFICE_OSM_INDEX || '0',
    SIMPLEOFFICE_DATALOGGER: process.env.SIMPLEOFFICE_DATALOGGER || '0'
  };

  backend = spawn(launch.command, launch.args, {
    cwd: dataDir,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  });
  backend.stdout?.on('data', (chunk) => console.log(`[python] ${chunk.toString().trimEnd()}`));
  backend.stderr?.on('data', (chunk) => console.error(`[python] ${chunk.toString().trimEnd()}`));
  backend.on('exit', (code, signal) => {
    backend = null;
    if (!quitting && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadFile(path.join(__dirname, 'loading.html'), {
        query: { error: `Python-Backend wurde beendet (${signal || code ?? 'unbekannt'}).` }
      }).catch(() => {});
    }
  });
  backend.on('error', (error) => {
    console.error('Python backend start failed:', error);
  });
}

async function waitForBackend(timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!backend || backend.exitCode !== null) {
      throw new Error('Python-Backend konnte nicht gestartet werden.');
    }
    try {
      const response = await fetch(backendUrl, { redirect: 'manual', signal: AbortSignal.timeout(1200) });
      if (response.status >= 200 && response.status < 500) return;
    } catch (_) {
      // Server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('Python-Backend antwortet nicht innerhalb des Startlimits.');
}

function isLocalUrl(value) {
  if (!backendUrl) return false;
  try {
    return new URL(value).origin === new URL(backendUrl).origin;
  } catch (_) {
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 780,
    minHeight: 560,
    show: false,
    title: 'SimpleOffice4Me',
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false
    }
  });

  mainWindow.removeMenu();
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isLocalUrl(url)) return { action: 'allow' };
    if (/^https?:/i.test(url)) shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isLocalUrl(url)) {
      event.preventDefault();
      if (/^https?:/i.test(url)) shell.openExternal(url).catch(() => {});
    }
  });

  return mainWindow;
}

async function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  const processToStop = backend;
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(processToStop.pid), '/t'], { windowsHide: true, stdio: 'ignore' });
  } else {
    processToStop.kill('SIGTERM');
  }
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, 5000);
    processToStop.once('exit', () => { clearTimeout(timer); resolve(); });
  });
  if (processToStop.exitCode === null) processToStop.kill('SIGKILL');
}

app.whenReady().then(async () => {
  const window = createWindow();
  await window.loadFile(path.join(__dirname, 'loading.html'));

  session.defaultSession.setPermissionCheckHandler((_webContents, permission, requestingOrigin) => {
    return isLocalUrl(requestingOrigin) && permission === 'media';
  });
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(Boolean(webContents && isLocalUrl(webContents.getURL()) && permission === 'media'));
  });

  try {
    await startBackend();
    await waitForBackend();
    await window.loadURL(backendUrl);
  } catch (error) {
    console.error(error);
    await window.loadFile(path.join(__dirname, 'loading.html'), {
      query: { error: error instanceof Error ? error.message : String(error) }
    });
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendUrl) {
    const window = createWindow();
    window.loadURL(backendUrl).catch(() => {});
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', (event) => {
  if (quitting) return;
  quitting = true;
  if (backend && backend.exitCode === null) {
    event.preventDefault();
    stopBackend().finally(() => app.quit());
  }
});
