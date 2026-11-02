const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const appSource = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../web/scan-capture-settings.js'), 'utf8');

assert.match(html, /<dialog id="scan-capture-settings-dialog"[^>]*class="device-settings-dialog"/,
  'the 360 capture settings reuse the device settings dialog surface');
assert.match(html, /id="scan-capture-step"/, 'the dialog exposes a rotation step select');
assert.match(html, /id="scan-capture-tilt"[^>]*min="-10"[^>]*max="10"/,
  'the tilt control is bounded by the safe ±10 degree table limit');
assert.match(html, /id="scan-capture-settings-start"/, 'the dialog carries an explicit start action');
assert.doesNotMatch(html.match(/<dialog id="scan-capture-settings-dialog"[\s\S]*?<\/dialog>/)?.[0] || '',
  /\stitle=/i, 'the capture settings dialog uses aria-label, not native hover tooltips');
assert.match(appSource, /CadyxScanCaptureSettings\?\.open\(\)/,
  'the Сканировать 360° button opens the settings dialog before starting');
assert.match(appSource, /scanner_device_id:captureSettings\.scanner_device_id,turntable_device_id:captureSettings\.turntable_device_id,step_degrees:captureSettings\.step_degrees,tilt_degrees:captureSettings\.tilt_degrees/,
  'the confirmed dialog values drive the 360 capture start request');

function fixture(fetch) {
  const elements = new Map();
  function element(id) {
    const listeners = new Map();
    const node = {
      id, dataset: {}, value: '', disabled: false, open: false, textContent: '',
      addEventListener(name, callback) {
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(callback);
      },
      fire(name, event = {}) {
        return Promise.all((listeners.get(name) || []).map(callback => callback(event)));
      },
      replaceChildren(...children) {
        this.children = children;
        const selected = children.find(child => child.selected) || children[0];
        this.value = selected ? selected.value : '';
      },
      showModal() { this.open = true; },
      close() { this.open = false; },
      click() { return this.fire('click'); },
    };
    elements.set(id, node);
    return node;
  }
  ['scan-capture-settings-dialog', 'scan-capture-settings-close', 'scan-capture-settings-cancel',
   'scan-capture-settings-start', 'scan-capture-devices', 'scan-capture-step',
   'scan-capture-tilt', 'scan-capture-tilt-value', 'scan-capture-settings-status',
  ].forEach(element);
  const sandbox = {
    document: { querySelector: selector => elements.get(selector.slice(1)) },
    Option: class { constructor(label, value) { this.label = label; this.value = value; this.selected = false; } },
    fetch, URLSearchParams, Number, Math, Promise, Error,
    window: {},
  };
  vm.runInNewContext(source, sandbox, { filename: 'scan-capture-settings.js' });
  return { get: id => elements.get(id), sandbox, api: sandbox.window.CadyxScanCaptureSettings };
}

const tick = () => new Promise(resolve => setImmediate(resolve));
const response = payload => ({ ok: true, json: async () => payload });
const savedSettings = {
  configured: true, scanner_device_id: 'scanner-1', turntable_device_id: 'table-1',
  step_degrees: 15, tilt_degrees: 5.0,
};

async function main() {
  // Without a selected scanner/table pair the dialog explains the blocker and resolves null.
  {
    const f = fixture(async () => { throw Error('no requests expected'); });
    const result = await f.api.open();
    assert.equal(result, null);
    assert.equal(f.get('scan-capture-settings-dialog').open, true, 'the dialog still opens to show the hint');
    assert.equal(f.get('scan-capture-settings-start').disabled, true);
    assert.match(f.get('scan-capture-devices').textContent, /не выбраны/);
    f.get('scan-capture-settings-dialog').close();
  }

  // With a selected pair the dialog loads the saved settings and starts with them.
  {
    const requests = [];
    const fetch = async (url, options = {}) => {
      requests.push([url, options]);
      if (url === '/api/v1/scanner/devices') {
        return response({
          scanners: [{ device_id: 'scanner-1', profile_name: 'Revopoint MINI' }],
          turntables: [{ device_id: 'table-1', profile_name: 'Двухосевой стол' }],
        });
      }
      if (url.startsWith('/api/v1/scanner/scan-capture-settings') && options.method !== 'POST') {
        return response(savedSettings);
      }
      if (url === '/api/v1/scanner/scan-capture-settings' && options.method === 'POST') {
        return response({ ok: true });
      }
      throw Error('Unexpected request: ' + url);
    };
    const f = fixture(fetch);
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => 'revopoint-dual-axis-table',
    };
    const pending = f.api.open();
    for (let i = 0; i < 8; i++) await tick();
    assert.equal(f.get('scan-capture-settings-dialog').open, true);
    assert.match(f.get('scan-capture-devices').textContent, /Revopoint MINI/);
    assert.match(f.get('scan-capture-devices').textContent, /Двухосевой стол/);
    assert.equal(f.get('scan-capture-step').value, '15', 'the saved rotation step is preselected');
    assert.equal(f.get('scan-capture-tilt').value, '5');
    assert.equal(f.get('scan-capture-tilt-value').textContent, '5°');
    assert.equal(f.get('scan-capture-settings-start').disabled, false);

    await f.get('scan-capture-settings-start').click();
    for (let i = 0; i < 4; i++) await tick();
    const post = requests.find(([url, options]) => url === '/api/v1/scanner/scan-capture-settings' && options.method === 'POST');
    assert.ok(post, 'start persists the per-pair settings');
    const body = JSON.parse(post[1].body);
    assert.deepEqual(body, {
      scanner_device_id: 'scanner-1', turntable_device_id: 'table-1',
      profile_id: 'revopoint-dual-axis-table', step_degrees: 15, tilt_degrees: 5,
    });
    const confirmed = await pending;
    assert.equal(confirmed.scanner_device_id, 'scanner-1');
    assert.equal(confirmed.turntable_device_id, 'table-1');
    assert.equal(confirmed.step_degrees, 15);
    assert.equal(confirmed.tilt_degrees, 5);
    assert.equal(f.get('scan-capture-settings-dialog').open, false);
  }

  // Cancel resolves null without a save request.
  {
    const requests = [];
    const fetch = async (url) => {
      requests.push(url);
      if (url === '/api/v1/scanner/devices') return response({ scanners: [], turntables: [] });
      if (url.startsWith('/api/v1/scanner/scan-capture-settings')) return response(savedSettings);
      throw Error('Unexpected request: ' + url);
    };
    const f = fixture(fetch);
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => '',
    };
    const pending = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await pending, null);
    assert.ok(!requests.some(url => url === '/api/v1/scanner/scan-capture-settings'),
      'cancel never persists settings');
  }

  // Device changes while the dialog is open must not save settings to another pair.
  {
    const requests = [];
    let tableId = 'table-1';
    const f = fixture(async (url, options = {}) => {
      requests.push([url, options]);
      if (url === '/api/v1/scanner/devices') return response({ scanners: [], turntables: [] });
      if (url.startsWith('/api/v1/scanner/scan-capture-settings') && options.method !== 'POST') {
        return response(savedSettings);
      }
      if (options.method === 'POST') return response({ ok: true });
      throw Error('Unexpected request: ' + url);
    });
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => tableId,
      turntableProfileId: () => 'revopoint-dual-axis-table',
    };
    const pending = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    tableId = 'table-2';
    await f.get('scan-capture-settings-start').click();
    assert.equal(requests.filter(([, opts]) => opts.method === 'POST').length, 0);
    assert.match(f.get('scan-capture-settings-status').textContent, /изменились/);
    assert.equal(f.get('scan-capture-settings-dialog').open, true);
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await pending, null);
  }

  console.log('scan capture settings dialog tests passed');
}

main().catch(error => { console.error(error); process.exit(1); });
