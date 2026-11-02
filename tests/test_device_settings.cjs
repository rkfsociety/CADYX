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
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(stale.get('scanner-device-select').value, 'scanner-1');
  oldDiscovery.resolve(response({ scanners: [], turntables: [] }));
  for (let i = 0; i < 4; i++) await tick();
  assert.equal(stale.get('scanner-device-select').value, 'scanner-1',
    'late discovery from a closed dialog must not erase current device choices');
  assert.equal(stale.get('device-settings-save').disabled, false);
  console.log('Device settings: keyboard tabs, persisted period, single save and stale discovery passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
