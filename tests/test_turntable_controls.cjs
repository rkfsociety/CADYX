const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const style = fs.readFileSync(path.join(__dirname, '../web/style.css'), 'utf8');
const workspaceSource = fs.readFileSync(path.join(__dirname, '../web/ply-preview.js'), 'utf8');
const turntableSource = fs.readFileSync(path.join(__dirname, '../web/turntable-controls.js'), 'utf8');
assert.match(html, /id="turntable-control-status"[^>]*hidden/,
  'the turntable status output starts hidden instead of showing helper copy');
assert.doesNotMatch(html, /\stitle=/i,
  'the panel markup has no native hover tooltips');
assert.doesNotMatch(html, /id="turntable-control-refresh"/,
  'hardware position refresh is automatic, without a refresh button');
assert.doesNotMatch(turntableSource, /Положение устарело|нажмите ↻|Нажмите ↻|Стол подключён/,
  'the turntable does not show stale-state prompts or routine status chatter');
assert.doesNotMatch(html, /folder-preview-hint|Перетаскивайте для поворота|Меньше секунд — быстрее/,
  'the panel does not display generic preview or device tips');
assert.match(html, /id="turntable-control-panel"[^>]*aria-label="Управление поворотным столом"/);
const turntablePanelHtml = html.match(/<aside id="turntable-control-panel"[\s\S]*?<\/aside>/)?.[0] || '';
assert.doesNotMatch(turntablePanelHtml, /<span>(?:Поворот|Наклон)<\/span>/,
  'icons replace redundant rotation and tilt labels');
const turntablePanelCss = style.match(/\.turntable-control-panel\s*\{([^}]+)\}/)?.[1] || '';
assert.match(turntablePanelCss, /position:\s*absolute/);
assert.match(turntablePanelCss, /top:\s*12px/);
assert.match(turntablePanelCss, /right:\s*12px/);
assert.match(turntablePanelCss, /width:\s*min\(380px,\s*calc\(100% - 24px\)\)/,
  'the turntable card stays compact in the preview corner');
assert.match(turntablePanelCss, /font:\s*14px\/1\.2 var\(--ui-font\)/,
  'panel copy uses one UI font family and size');
assert.match(style, /\.turntable-control-panel select[^}]*font:\s*700 14px var\(--ui-font\)/);
assert.match(style, /\.turntable-control-panel \.button[^}]*font:\s*680 14px var\(--ui-font\)/);
assert.doesNotMatch(style, /\.turntable-spin-label[^}]*text-overflow:\s*ellipsis/,
  'the continuous-rotation label is not clipped');
assert.match(style, /\.turntable-control-panel \.turntable-spin-button\[aria-pressed="false"\][^{]*\{[^}]*#03bad5/);
assert.match(style, /\.turntable-control-panel \.turntable-spin-button\[aria-pressed="true"\][^{]*\{[^}]*#8b9ba7/);
assert.match(style, /\.turntable-control-panel \.turntable-stop-button\[data-active="true"\][^{]*\{[^}]*#bd2f4e/);
assert.match(html, /<div class="viewport" id="viewport">[\s\S]*?<aside id="turntable-control-panel"[^>]*>[\s\S]*?<\/aside>[\s\S]*?<\/div>\s*<footer class="viewport-footer"/,
  'the turntable card stays inside the preview container');
assert.match(html, /class="turntable-control-card"/,
  'rotation and tilt values use the large paired status cards');
assert.match(html, /turntable-control-rates/,
  'step and revolution controls share a row');
const turntableActionCss = style.match(/\.turntable-control-actions\s*\{([^}]+)\}/)?.[1] || '';
assert.match(turntableActionCss, /grid-template-columns:\s*80px minmax\(0, 1fr\)/);
assert.match(html, /turntable-control-position[\s\S]*?id="turntable-zero"/,
  'the zero control sits in the top value row');
assert.match(html, /\.primary-grid\[data-view="scanning"\] \.model-toolbar #viewport-camera-presets\s*\{\s*display:\s*flex !important;/,
  'standard camera views stay available in the Scanning toolbar');
assert.doesNotMatch(html, /\.primary-grid\[data-view="scanning"\] \.model-toolbar #viewport-camera-presets\s*,/,
  'the Scanning layout does not hide the standard camera views');
assert.match(workspaceSource,
  /document\.querySelectorAll\("#project-detail button"\)\.forEach\(\(button\) => \{\s*if \(button\.closest\("#turntable-control-panel"\)\) return;/,
  'workspace lock refreshes leave turntable controls to their own state controller');

const handlers = new Map();
const calls = [];
const makeElement = () => {
  let text = '';
  return {
    dataset: {}, value: '', disabled: false, hidden: false, style: { values: {}, setProperty(name, value) { this.values[name] = value; } },
    attributes: {}, attributeWrites: {}, textWrites: 0,
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
  'turntable-tilt-target', 'turntable-tilt-target-value', 'turntable-control-status', 'turntable-spin', 'turntable-spin-label',
  'viewport-scan-360', 'primary-grid', 'turntable-rotate-left', 'turntable-rotate-right',
  'turntable-tilt-apply', 'turntable-zero', 'turntable-stop',
  'viewport-live-camera',
];
const elements = Object.fromEntries(ids.map(id => [id, Object.assign(makeElement(), { id })]));
elements['turntable-step'].value = '15';
elements['turntable-tilt-target'].value = '0';
elements['viewport-live-camera'].checked = true;
elements['viewport-live-camera'].setAttribute('aria-pressed', 'true');
elements['primary-grid'].dataset.view = 'scanning';
const panelSelectors = {
  '#turntable-step': 'turntable-step',
  '#turntable-speed': 'turntable-speed', '#turntable-rotate-left': 'turntable-rotate-left',
  '#turntable-rotate-right': 'turntable-rotate-right', '#turntable-tilt-target': 'turntable-tilt-target',
  '#turntable-tilt-apply': 'turntable-tilt-apply', '#turntable-zero': 'turntable-zero',
  '#turntable-spin': 'turntable-spin', '#turntable-stop': 'turntable-stop',
};
elements['turntable-control-panel'].querySelector = selector => elements[panelSelectors[selector]];
elements['turntable-spin'].querySelector = selector => selector === '.turntable-spin-label'
  ? elements['turntable-spin-label'] : null;
const document = { querySelector: selector => selector === '.primary-grid' ? elements['primary-grid'] : elements[selector.slice(1)] };
const pollDelays = [];
const scheduledTimers = [];
let currentSpinState = 'idle';
let currentPositionAge = 0;
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
    position: currentSpinState === 'idle'
      ? { command: 'query', angle_degrees: 12, tilt_degrees: 0, rotation_period_seconds: 17.5,
        age_seconds: currentPositionAge } : null,
  } }; } };
  const body = JSON.parse(options.body);
  let result = { state: 'completed', command: body.command, output: `${body.command} completed`,
    turntable_device_id: body.device_id, profile_id: body.profile_id };
  if (body.command === 'query') {
    currentPositionAge = 0;
    result.angle_degrees = 12;
    result.tilt_degrees = 0;
    result.rotation_period_seconds = 18;
  }
  if (body.command === 'spin-start') { result.state = 'starting'; currentSpinState = 'starting'; }
  if (body.command === 'spin-stop') { result.state = 'stopping'; currentSpinState = 'stopping'; }
  return { ok: true, async json() { return { result }; } };
};
const context = { window, document, fetch, URLSearchParams,
  setTimeout: (callback, delay) => { pollDelays.push(delay); scheduledTimers.push({ callback, delay }); return scheduledTimers.length; },
  clearTimeout() {} };
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../web/turntable-controls.js'), 'utf8'), context);
window.CadyxTurntableControls.initialize();
const wait = () => new Promise(resolve => setImmediate(resolve));
const waitUntil = async predicate => {
  for (let attempt = 0; attempt < 20 && !predicate(); attempt += 1) await wait();
  assert.ok(predicate(), 'the asynchronous turntable control request completed');
};
const pollStatus = async () => {
  const getCount = calls.filter(call => !call.options.method).length;
  const timer = scheduledTimers.filter(item => item.delay === 3000).at(-1);
  assert.ok(timer, 'automatic status polling is scheduled');
  timer.callback();
  await waitUntil(() => calls.filter(call => !call.options.method).length > getCount);
  await wait();
};

(async () => {
  await wait();
  assert.equal(elements['turntable-angle'].textContent, '12.0°');
  assert.equal(elements['turntable-control-status'].hidden, true,
    'idle turntable state does not show helper text');
  assert.equal(elements['turntable-stop'].dataset.active, 'false',
    'the stop control is neutral while the table is idle');
  assert.equal(elements['turntable-stop'].disabled, true,
    'the stop control is disabled while the table is idle');
  assert.equal(calls.some(call => call.options.method === 'POST'), false,
    'opening the panel reads cached state without contacting or moving hardware');
  assert.equal(elements['turntable-speed'].value, '18', 'the closest safe speed preset remains selected');
  assert.equal(elements['turntable-spin'].disabled, false,
    'continuous rotation is enabled when the scanner preview is live');
  currentPositionAge = 8;
  await pollStatus();
  await waitUntil(() => calls.some(call => call.options.method === 'POST'
    && JSON.parse(call.options.body).command === 'query'));
  assert.equal(currentPositionAge, 0, 'a stale position triggers an automatic hardware query');
  elements['viewport-live-camera'].setAttribute('aria-pressed', 'false');
  await pollStatus();
  assert.equal(elements['turntable-spin'].disabled, true,
    'continuous rotation is disabled when the scanner preview is stopped');
  elements['viewport-live-camera'].setAttribute('aria-pressed', 'true');
  await pollStatus();
  assert.equal(elements['turntable-spin'].disabled, false,
    'continuous rotation is re-enabled only after the scanner preview is ready');
  await handlers.get('turntable-rotate-right:click')();
  await waitUntil(() => calls.filter(call => call.options.method === 'POST').some(call =>
    JSON.parse(call.options.body).command === 'rotate'));
  let controlCall = calls.filter(call => call.options.method === 'POST').filter(call =>
    JSON.parse(call.options.body).command === 'rotate').at(-1);
  assert.equal(JSON.parse(controlCall.options.body).value, 15);
  assert.equal(JSON.parse(controlCall.options.body).command, 'rotate');
  await waitUntil(() => !elements['turntable-rotate-right'].disabled);

  elements['turntable-speed'].value = '45';
  await handlers.get('turntable-speed:change')();
  await waitUntil(() => window.CadyxDeviceSettings.savedSpeed === 45);
  await waitUntil(() => !elements['turntable-rotate-right'].disabled);
  assert.equal(window.CadyxDeviceSettings.savedSpeed, 45);
  assert.ok(calls.some(call => call.url === '/api/v1/scanner/turntable/settings'));

  await handlers.get('turntable-spin:click')();
  await waitUntil(() => calls.filter(call => call.options.method === 'POST').some(call =>
    JSON.parse(call.options.body).command === 'spin-start'));
  await waitUntil(() => elements['turntable-spin'].attributes['aria-pressed'] === 'true');
  assert.equal(elements['turntable-spin'].attributes['aria-pressed'], 'true');
  assert.equal(elements['turntable-spin-label'].textContent, 'Подключение…',
    'the control reports connection startup instead of claiming the table is already moving');
  assert.equal(elements['turntable-stop'].disabled, true,
    'the stop control stays disabled while rotation is starting');
  assert.equal(elements['viewport-scan-360'].dataset.turntableBusy, 'true');
  currentSpinState = 'running';
  await scheduledTimers.filter(timer => timer.delay === 250).at(-1).callback();
  await waitUntil(() => elements['turntable-spin-label'].textContent === 'Остановить вращение');
  assert.equal(elements['turntable-stop'].dataset.active, 'true',
    'the stop control turns red only while continuous rotation is active');
  assert.equal(elements['turntable-control-status'].hidden, true,
    'active rotation is represented by the control state, without redundant status copy');
  assert.equal(elements['turntable-stop'].disabled, false,
    'the stop control enables while continuous rotation is running');
  const postsBeforeSpinPoll = calls.filter(call => call.options.method === 'POST').length;
  await pollStatus();
  const spinTextWrites = elements['turntable-spin-label'].textWrites;
  const spinPressedWrites = elements['turntable-spin'].attributeWrites['aria-pressed'];
  assert.equal(calls.filter(call => call.options.method === 'POST').length, postsBeforeSpinPoll,
    'running rotation uses spin telemetry without issuing idle-position queries');
  assert.equal(elements['turntable-spin-label'].textWrites, spinTextWrites,
    'a status query does not recreate the running button label');
  assert.equal(elements['turntable-spin'].attributeWrites['aria-pressed'], spinPressedWrites,
    'a status query does not toggle the pressed attribute');
  await handlers.get('turntable-stop:click')();
  await waitUntil(() => calls.filter(call => call.options.method === 'POST').some(call =>
    JSON.parse(call.options.body).command === 'spin-stop'));
  assert.equal(JSON.parse(calls.filter(call => call.options.method === 'POST').at(-1).options.body).command, 'spin-stop');
  await waitUntil(() => elements['turntable-stop'].dataset.active === 'false');
  assert.equal(elements['turntable-stop'].disabled, true);
  assert.equal(elements['turntable-stop'].dataset.active, 'false',
    'the stop control returns to neutral when rotation stops');
  assert.ok(calls.some(call => call.url.startsWith('/api/v1/scanner/turntable/control')));
  console.log('turntable controls passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
