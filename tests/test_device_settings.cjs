const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/device-settings.js'), 'utf8');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture(fetch) {
  const elements = new Map();
  function element(id) {
    const listeners = new Map();
    const node = {
      id, dataset: {}, attributes: {}, value: '', disabled: false, hidden: false,
      files: [], open: false, textContent: '', tabIndex: 0,
      addEventListener(name, callback) {
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(callback);
      },
      fire(name, event = {}) {
        return Promise.all((listeners.get(name) || []).map(callback => callback(event)));
      },
      setAttribute(name, value) { this.attributes[name] = value; },
      replaceChildren(...children) { this.children = children; },
      add(option) { this.children.push(option); },
      focus() { this.focused = true; },
      click() { return this.fire('click'); },
      showModal() { this.open = true; },
      close() { this.open = false; return this.fire('close'); },
    };
    elements.set(id, node);
    return node;
  }
  const ids = [
    'device-settings-dialog', 'scanning-devices', 'device-settings-close',
    'device-settings-save', 'device-settings-refresh', 'scanner-device-select',
    'turntable-device-select', 'scanner-q-matrix', 'scanner-q-matrix-replace',
    'scanner-depth-scale', 'turntable-rotation-period', 'device-settings-status',
    'scanner-calibration-status',
  ];
  ids.forEach(element);
  const scanner = element('device-tab-scanner');
  const turntable = element('device-tab-turntable');
  scanner.dataset.deviceTab = 'scanner';
  turntable.dataset.deviceTab = 'turntable';
  const scannerPanel = element('device-panel-scanner');
  const turntablePanel = element('device-panel-turntable');
  scannerPanel.dataset.devicePanel = 'scanner';
  turntablePanel.dataset.devicePanel = 'turntable';
  elements.get('device-settings-dialog').querySelectorAll = selector => {
    if (selector === '[data-device-tab]') return [scanner, turntable];
    if (selector === '[data-device-panel]') return [scannerPanel, turntablePanel];
    throw new Error('Unexpected selector: ' + selector);
  };
  const storage = new Map();
  const sandbox = {
    document: { querySelector: selector => elements.get(selector.slice(1)) },
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
    },
    Option: class { constructor(label, value) { this.label = label; this.value = value; } },
    fetch, URLSearchParams, Uint8Array, btoa: value => Buffer.from(value, 'binary').toString('base64'),
    window: {},
  };
  vm.runInNewContext(source, sandbox, { filename: 'device-settings.js' });
  return { get: id => elements.get(id), storage, api: sandbox.window.CadyxDeviceSettings };
}

const tick = () => new Promise(resolve => setImmediate(resolve));
const response = payload => ({ ok: true, json: async () => payload });
const discovered = {
  scanners: [{ device_id: 'scanner-1', profile_name: 'Mini', calibrated: true }],
  turntables: [{ device_id: 'table-1', profile_name: 'Table', profile_id: 'table-profile' }],
};

async function main() {
  const postGate = deferred();
  let posts = 0;
  const fetch = async (url, options = {}) => {
    if (url === '/api/v1/scanner/devices') return response(discovered);
    if (url.startsWith('/api/v1/scanner/calibration?')) return response({ depth_scale_mm_per_raw_unit: 0.1 });
    if (url.startsWith('/api/v1/scanner/turntable/settings') && options.method !== 'POST') {
      return response({ configured: true, turntable_device_id: 'table-1', profile_id: 'table-profile', rotation_period_seconds: 30 });
    }
    if (url === '/api/v1/scanner/turntable/settings' && options.method === 'POST') {
      posts += 1;
      await postGate.promise;
      return response({});
    }
    throw Error('Unexpected request: ' + url);
  };
  const f = fixture(fetch);
  await f.get('scanning-devices').fire('click');
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(f.get('device-settings-save').disabled, false, 'save should enable after discovery');

  let prevented = false;
  await f.get('device-tab-scanner').fire('keydown', { key: 'ArrowRight', preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(f.get('device-tab-turntable').attributes['aria-selected'], 'true');
  assert.equal(f.get('device-panel-scanner').hidden, true);
  assert.equal(f.get('device-tab-turntable').focused, true);
  await f.get('device-tab-turntable').fire('keydown', { key: 'Home', preventDefault() {} });
  assert.equal(f.get('device-tab-scanner').attributes['aria-selected'], 'true');

  f.get('turntable-rotation-period').value = '45';
  const first = f.get('device-settings-save').fire('click');
  await tick();
  assert.equal(f.get('device-settings-save').disabled, true, 'save should remain disabled during the POST');
  await f.get('device-settings-save').fire('click');
  assert.equal(posts, 1, 'rapid clicks must not duplicate hardware writes');
  postGate.resolve();
  await first;
  assert.equal(f.get('device-settings-save').disabled, false);
  const saved = JSON.parse(f.storage.get('cadyx-device-settings-v1'));
  assert.equal(saved.turntableRotationPeriodSeconds, 45, 'rotation period must survive a dialog reopen');
  assert.equal(f.api.turntableRotationPeriodSeconds(), 45);

  const oldDiscovery = deferred();
  let discoveryCalls = 0;
  const stale = fixture(async (url, options = {}) => {
    if (url === '/api/v1/scanner/devices') {
      discoveryCalls += 1;
      return discoveryCalls === 1 ? oldDiscovery.promise : response(discovered);
    }
    if (url.startsWith('/api/v1/scanner/calibration?')) return response({ depth_scale_mm_per_raw_unit: 0.1 });
    if (url.startsWith('/api/v1/scanner/turntable/settings') && options.method !== 'POST') {
      return response({ configured: true, turntable_device_id: 'table-1', profile_id: 'table-profile', rotation_period_seconds: 30 });
    }
    throw Error('Unexpected stale-discovery request: ' + url);
  });
  await stale.get('scanning-devices').fire('click');
  await stale.get('device-settings-dialog').close();
  await stale.get('scanning-devices').fire('click');
  // HTMLDialogElement's close event can be delivered after the next showModal.
  await stale.get('device-settings-dialog').fire('close');
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(stale.get('scanner-device-select').value, 'scanner-1');
  oldDiscovery.resolve(response({ scanners: [], turntables: [] }));
  for (let i = 0; i < 4; i++) await tick();
  assert.equal(stale.get('scanner-device-select').value, 'scanner-1',
    'late discovery from a closed dialog must not erase current device choices');
  assert.equal(stale.get('device-settings-save').disabled, false);

  // Closing during calibration must not start a table write or persist a stale selection.
  const calibrationGate = deferred();
  let calibrationPosts = 0, tablePosts = 0;
  const canceled = fixture(async (url, options = {}) => {
    if (url === '/api/v1/scanner/devices') return response({
      ...discovered, scanners: [{ ...discovered.scanners[0], calibrated: false }],
    });
    if (url.startsWith('/api/v1/scanner/turntable/settings') && options.method !== 'POST')
      return response({ configured: true, turntable_device_id: 'table-1',
        profile_id: 'table-profile', rotation_period_seconds: 30 });
    if (url === '/api/v1/scanner/calibration' && options.method === 'POST') {
      calibrationPosts += 1; await calibrationGate.promise; return response({});
    }
    if (url === '/api/v1/scanner/turntable/settings' && options.method === 'POST') {
      tablePosts += 1; return response({});
    }
    throw Error('Unexpected canceled-save request: ' + url);
  });
  await canceled.get('scanning-devices').fire('click');
  for (let i = 0; i < 6; i++) await tick();
  canceled.get('scanner-q-matrix').files = [{ size: 64, arrayBuffer: async () => new Uint8Array(64).buffer }];
  canceled.get('scanner-depth-scale').value = '0.1';
  const pending = canceled.get('device-settings-save').fire('click');
  await tick();
  assert.equal(calibrationPosts, 1);
  assert.equal(canceled.get('device-settings-refresh').disabled, true);
  assert.equal(canceled.get('scanner-device-select').disabled, true);
  assert.equal(canceled.get('turntable-device-select').disabled, true);
  await canceled.get('device-settings-dialog').close();
  calibrationGate.resolve();
  await pending;
  assert.equal(tablePosts, 0, 'canceled calibration must not start a table write');
  const canceledSettings = JSON.parse(canceled.storage.get('cadyx-device-settings-v1') || '{}');
  assert.equal(canceledSettings.scannerDeviceId || '', '',
    'canceled save must not persist a scanner selection');

  // Delayed GET responses cannot overwrite a period edited in the dialog.
  const oldTableSettings = deferred();
  let settingsReads = 0;
  const edited = fixture(async (url, options = {}) => {
    if (url === '/api/v1/scanner/devices') return response(discovered);
    if (url.startsWith('/api/v1/scanner/calibration?')) return response({ depth_scale_mm_per_raw_unit: 0.1 });
    if (url.startsWith('/api/v1/scanner/turntable/settings') && options.method !== 'POST') {
      settingsReads += 1;
      return settingsReads === 1
        ? response({ configured: true, turntable_device_id: 'table-1',
          profile_id: 'table-profile', rotation_period_seconds: 30 })
        : oldTableSettings.promise;
    }
    throw Error('Unexpected period-edit request: ' + url);
  });
  await edited.get('scanning-devices').fire('click');
  for (let i = 0; i < 6; i++) await tick();
  edited.get('turntable-rotation-period').value = '45';
  await edited.get('turntable-rotation-period').fire('input');
  oldTableSettings.resolve(response({ configured: true, turntable_device_id: 'table-1',
    profile_id: 'table-profile', rotation_period_seconds: 60 }));
  for (let i = 0; i < 4; i++) await tick();
  assert.equal(edited.get('turntable-rotation-period').value, '45',
    'late settings must not overwrite manual input');
  console.log('Device settings: tabs, save cancellation, stale discovery and period edits passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
