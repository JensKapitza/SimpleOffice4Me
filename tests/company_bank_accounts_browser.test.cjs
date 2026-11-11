'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {existsSync} = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

const browser = [process.env.CHROME_BIN, '/usr/bin/chromium', '/usr/bin/google-chrome']
  .filter(Boolean).find(executable => existsSync(executable));

test('bank-account buttons initialize in real browser with script in header', {skip: !browser && 'Chromium missing'}, () => {
  const fixture = pathToFileURL(path.join(__dirname, 'fixtures/company_bank_accounts_browser.html')).href;
  const run = spawnSync(browser, ['--headless', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--virtual-time-budget=5000', '--dump-dom', fixture],
    {encoding: 'utf8', timeout: 20000});
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stdout, /id="result">PASS</);
});
