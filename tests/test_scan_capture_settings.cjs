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

  // A canceled save may finish after a new dialog opens. Its late response must
  // not close or resolve the new dialog with the previous capture settings.
  {
    let finishOldSave;
    let saves = 0;
    const f = fixture(async (url, options = {}) => {
      if (url === '/api/v1/scanner/devices') return response({ scanners: [], turntables: [] });
      if (url.startsWith('/api/v1/scanner/scan-capture-settings') && options.method !== 'POST') {
        return response(savedSettings);
      }
      if (options.method === 'POST') {
        saves += 1;
        return new Promise(resolve => { finishOldSave = resolve; });
      }
      throw Error('Unexpected request: ' + url);
    });
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => 'revopoint-dual-axis-table',
    };
    const first = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    const saving = f.get('scan-capture-settings-start').click();
    await tick();
    assert.equal(saves, 1);
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await first, null);
    const second = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    finishOldSave(response({ ok: true }));
    await saving;
    assert.equal(f.get('scan-capture-settings-dialog').open, true,
      'a stale save must not close the newer dialog');
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await second, null);
  }

  // Capture settings are a snapshot at submit time, not values read after
  // a delayed POST; the controls are disabled until the request completes.
  {
    let finishSave;
    let submitted;
    const f = fixture(async (url, options = {}) => {
      if (url === '/api/v1/scanner/devices') return response({ scanners: [], turntables: [] });
      if (url.startsWith('/api/v1/scanner/scan-capture-settings') && options.method !== 'POST') {
        return response(savedSettings);
      }
      if (options.method === 'POST') {
        submitted = JSON.parse(options.body);
        return new Promise(resolve => { finishSave = resolve; });
      }
      throw Error('Unexpected request: ' + url);
    });
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => 'revopoint-dual-axis-table',
    };
    const pending = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    const saving = f.get('scan-capture-settings-start').click();
    await tick();
    assert.equal(f.get('scan-capture-step').disabled, true);
    assert.equal(f.get('scan-capture-tilt').disabled, true);
    f.get('scan-capture-step').value = '30';
    f.get('scan-capture-tilt').value = '9';
    finishSave(response({ ok: true }));
    await saving;
    const confirmed = await pending;
    assert.equal(submitted.step_degrees, 15);
    assert.equal(submitted.tilt_degrees, 5);
    assert.equal(confirmed.step_degrees, 15);
    assert.equal(confirmed.tilt_degrees, 5);
  }


  // Optional device discovery must not delay capture settings or leave a stale loading label.
  {
    let finishDeviceNames;
    const f = fixture(async (url) => {
      if (url === '/api/v1/scanner/devices') {
        return new Promise(resolve => { finishDeviceNames = resolve; });
      }
      if (url.startsWith('/api/v1/scanner/scan-capture-settings')) {
        return response({ ...savedSettings, step_degrees: 17, tilt_degrees: 99 });
      }
      throw Error('Unexpected request: ' + url);
    });
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => '',
    };
    const pending = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    assert.equal(f.get('scan-capture-settings-start').disabled, false,
      'a slow optional device-name request must not block the start action');
    assert.equal(f.get('scan-capture-step').value, '30',
      'unsupported saved steps must fall back to a valid default');
    assert.equal(f.get('scan-capture-tilt').value, '10',
      'saved tilt outside the supported range must be clamped');
    assert.match(f.get('scan-capture-devices').textContent, /scanner-1/,
      'device identifiers remain visible until display names arrive');
    assert.doesNotMatch(f.get('scan-capture-settings-status').textContent, /Загружаю/,
      'the status must reflect completed settings loading');
    finishDeviceNames(response({
      scanners: [{ device_id: 'scanner-1', profile_name: 'Revopoint MINI' }],
      turntables: [{ device_id: 'table-1', profile_name: 'Двухосевой стол' }],
    }));
    for (let i = 0; i < 6; i++) await tick();
    assert.match(f.get('scan-capture-devices').textContent, /Revopoint MINI/);
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await pending, null);
  }

  // Invalid control values must be rejected locally without writing scanner settings.
  {
    let writes = 0;
    const f = fixture(async (url, options = {}) => {
      if (url === '/api/v1/scanner/devices') return response({ scanners: [], turntables: [] });
      if (url.startsWith('/api/v1/scanner/scan-capture-settings') && options.method !== 'POST') {
        return response(savedSettings);
      }
      if (options.method === 'POST') { writes += 1; return response({ ok: true }); }
      throw Error('Unexpected request: ' + url);
    });
    f.sandbox.window.CadyxDeviceSettings = {
      scannerDeviceId: () => 'scanner-1',
      turntableDeviceId: () => 'table-1',
      turntableProfileId: () => '',
    };
    const pending = f.api.open();
    for (let i = 0; i < 6; i++) await tick();
    f.get('scan-capture-step').value = '17';
    await f.get('scan-capture-settings-start').click();
    assert.equal(writes, 0, 'unsupported step must not reach the server');
    assert.equal(f.get('scan-capture-settings-start').disabled, false);
    f.get('scan-capture-step').value = '15';
    f.get('scan-capture-tilt').value = '11';
    await f.get('scan-capture-settings-start').click();
    assert.equal(writes, 0, 'out-of-range tilt must not reach the server');
    assert.match(f.get('scan-capture-settings-status').textContent, /допустимый шаг/);
    assert.equal(f.get('scan-capture-tilt').disabled, false);
    await f.get('scan-capture-settings-cancel').click();
    assert.equal(await pending, null);
  }

  console.log('scan capture settings dialog tests passed');
}

main().catch(error => { console.error(error); process.exit(1); });
