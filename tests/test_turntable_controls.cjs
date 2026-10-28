const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const style = fs.readFileSync(path.join(__dirname, '../web/style.css'), 'utf8');
assert.match(html, /id="turntable-control-panel"[^>]*aria-labelledby="turntable-control-title"/);
assert.match(style, /\.turntable-control-panel\s*\{[^}]*top:\s*12px;[^}]*right:\s*12px;[^}]*aspect-ratio:\s*1;/,
  'the square turntable controls are overlaid in the viewport upper-right corner');
assert.match(html, /\.primary-grid\[data-view="scanning"\] \.model-toolbar #viewport-camera-presets\s*\{\s*display:\s*flex !important;/,
  'standard camera views stay available in the Scanning toolbar');
assert.doesNotMatch(html, /\.primary-grid\[data-view="scanning"\] \.model-toolbar #viewport-camera-presets\s*,/,
  'the Scanning layout does not hide the standard camera views');

const handlers = new Map();
const calls = [];
const makeElement = () => {
  let text = '';
  return {
    dataset: {}, value: '', disabled: false, attributes: {}, attributeWrites: {}, textWrites: 0,
    get textContent() { return text; },
    set textContent(value) { this.textWrites += 1; text = value; },
    addEventListener(name, handler) { handlers.set(`${this.id || ''}:${name}`, handler); },
    getAttribute(name) { return this.attributes[name] ?? null; },
    setAttribute(name, value) {
      this.attributeWrites[name] = (this.attributeWrites[name] || 0) + 1;
      this.attributes[name] = value;
    },
  };
};
const ids = [
  'turntable-control-panel', 'turntable-angle', 'turntable-tilt', 'turntable-step', 'turntable-speed',
  'turntable-tilt-target', 'turntable-tilt-target-value', 'turntable-control-status', 'turntable-spin',
  'viewport-scan-360', 'primary-grid', 'turntable-control-refresh', 'turntable-rotate-left', 'turntable-rotate-right',
  'turntable-tilt-apply', 'turntable-zero', 'turntable-stop',
  'viewport-live-camera',
];
const elements = Object.fromEntries(ids.map(id => [id, Object.assign(makeElement(), { id })]));
elements['turntable-step'].value = '15';
elements['turntable-tilt-target'].value = '0';
elements['viewport-live-camera'].checked = true;
elements['primary-grid'].dataset.view = 'scanning';
const panelSelectors = {
  '#turntable-control-refresh': 'turntable-control-refresh', '#turntable-step': 'turntable-step',
  '#turntable-speed': 'turntable-speed', '#turntable-rotate-left': 'turntable-rotate-left',
  '#turntable-rotate-right': 'turntable-rotate-right', '#turntable-tilt-target': 'turntable-tilt-target',
  '#turntable-tilt-apply': 'turntable-tilt-apply', '#turntable-zero': 'turntable-zero',
  '#turntable-spin': 'turntable-spin', '#turntable-stop': 'turntable-stop',
};
elements['turntable-control-panel'].querySelector = selector => elements[panelSelectors[selector]];
const document = { querySelector: selector => selector === '.primary-grid' ? elements['primary-grid'] : elements[selector.slice(1)] };
const pollDelays = [];
let currentSpinState = 'idle';
const window = {
  CadyxDeviceSettings: {
    turntableDeviceId: () => '0123456789AB', turntableProfileId: () => 'revopoint-dual-axis-table',
    turntableRotationPeriodSeconds: () => 30, rememberTurntableRotationPeriodSeconds(value) { this.savedSpeed = value; },
  },
  cadyxSyncCaptureButton() {},
};
const fetch = async (url, options = {}) => {
  calls.push({ url, options });
  if (!options.method) return { ok: true, async json() { return { control: {
    spin: { state: currentSpinState, scan_active: false },
    position: { command: 'query', angle_degrees: 12, tilt_degrees: 0, rotation_period_seconds: 17.5 },
  } }; } };
  const body = JSON.parse(options.body);
  let result = { state: 'completed', command: body.command, output: `${body.command} completed`,
    turntable_device_id: body.device_id, profile_id: body.profile_id };
  if (body.command === 'spin-start') { result.state = 'starting'; currentSpinState = 'starting'; }
  if (body.command === 'spin-stop') { result.state = 'stopping'; currentSpinState = 'stopping'; }
  return { ok: true, async json() { return { result }; } };
};
const context = { window, document, fetch, URLSearchParams,
  setTimeout: (_callback, delay) => { pollDelays.push(delay); return 0; }, clearTimeout() {} };
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../web/turntable-controls.js'), 'utf8'), context);
window.CadyxTurntableControls.initialize();
const wait = () => new Promise(resolve => setImmediate(resolve));
const waitUntil = async predicate => {
  for (let attempt = 0; attempt < 20 && !predicate(); attempt += 1) await wait();
  assert.ok(predicate(), 'the asynchronous turntable control request completed');
};

(async () => {
  await wait();
  assert.equal(elements['turntable-angle'].textContent, '12.0°');
  assert.ok(pollDelays.includes(3000), 'live scanning refreshes table telemetry often enough to animate the preview');
  assert.equal(elements['turntable-speed'].value, '18', 'the closest safe speed preset remains selected');
  await handlers.get('turntable-rotate-right:click')();
  await waitUntil(() => calls.some(call => call.options.method === 'POST'));
  let controlCall = calls.find(call => call.options.method === 'POST');
  assert.equal(JSON.parse(controlCall.options.body).value, 15);
  assert.equal(JSON.parse(controlCall.options.body).command, 'rotate');
  await waitUntil(() => !elements['turntable-control-refresh'].disabled);

  elements['turntable-speed'].value = '45';
  await handlers.get('turntable-speed:change')();
  await waitUntil(() => window.CadyxDeviceSettings.savedSpeed === 45);
  await waitUntil(() => !elements['turntable-control-refresh'].disabled);
  assert.equal(window.CadyxDeviceSettings.savedSpeed, 45);
  assert.ok(calls.some(call => call.url === '/api/v1/scanner/turntable/settings'));

  await handlers.get('turntable-spin:click')();
  await waitUntil(() => calls.filter(call => call.options.method === 'POST').some(call =>
    JSON.parse(call.options.body).command === 'spin-start'));
  await waitUntil(() => elements['turntable-spin'].attributes['aria-pressed'] === 'true');
  assert.equal(elements['turntable-spin'].attributes['aria-pressed'], 'true');
  assert.equal(elements['turntable-spin'].textContent, 'ПОДКЛЮЧЕНИЕ…',
    'the control reports connection startup instead of claiming the table is already moving');
  assert.equal(elements['viewport-scan-360'].dataset.turntableBusy, 'true');
  currentSpinState = 'running';
  await handlers.get('turntable-control-refresh:click')();
  await waitUntil(() => elements['turntable-spin'].textContent === '■ ОСТАНОВИТЬ ВРАЩЕНИЕ');
  const spinTextWrites = elements['turntable-spin'].textWrites;
  const spinPressedWrites = elements['turntable-spin'].attributeWrites['aria-pressed'];
  await handlers.get('turntable-control-refresh:click')();
  await wait();
  assert.equal(elements['turntable-spin'].textWrites, spinTextWrites,
    'polling an unchanged running state does not recreate the button label');
  assert.equal(elements['turntable-spin'].attributeWrites['aria-pressed'], spinPressedWrites,
    'polling an unchanged running state does not toggle its pressed attribute');
  await handlers.get('turntable-stop:click')();
  await waitUntil(() => calls.filter(call => call.options.method === 'POST').some(call =>
    JSON.parse(call.options.body).command === 'spin-stop'));
  assert.equal(JSON.parse(calls.filter(call => call.options.method === 'POST').at(-1).options.body).command, 'spin-stop');
  assert.ok(calls.some(call => call.url.startsWith('/api/v1/scanner/turntable/control')));
  console.log('turntable controls passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
