const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const listeners = new Map();
const canvasListeners = new Map();
const buttons = ['isometric', 'front', 'right', 'top'].map((name) => ({
  dataset: {cameraPreset: name},
  disabled: false,
  attributes: {},
  setAttribute(key, value) { this.attributes[key] = value; },
}));
const root = {
  querySelectorAll: () => buttons,
  addEventListener: (type, callback) => listeners.set(type, callback),
};
const canvas = {addEventListener: (type, callback) => canvasListeners.set(type, callback)};
const calls = [];
const viewer = {
  getCamera: () => ({zoom: 2.5}),
  setCamera: (camera) => calls.push(camera),
};
const window = {
  CadyxViewportHelpers: {CAMERA_PRESETS: Object.freeze({
    isometric: {yaw: 1, pitch: 1}, front: {yaw: 0, pitch: 0},
    right: {yaw: 2, pitch: 0}, top: {yaw: 0, pitch: 2},
  })},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/browser-viewers.js'), 'utf8'),
  {window}, {filename:'browser-viewers.js'});
const controller = window.CadyxBrowserViewers.createCameraPresetController({root, canvas, viewer});

assert.equal(controller.apply('front'), true);
assert.equal(calls.length, 1);
assert.equal(calls[0].zoom, 2.5);
assert.equal(calls[0].projection, 'orthographic');
assert.equal(buttons[1].attributes['aria-pressed'], 'true');
assert.equal(buttons[0].attributes['aria-pressed'], 'false');

for (const invalid of ['constructor', '__proto__', 'toString', 'missing']) {
  assert.equal(controller.apply(invalid), false, 'inherited names are not camera presets');
}
assert.equal(calls.length, 1);

const click = listeners.get('click');
click({target: {closest: () => buttons[2]}});
assert.equal(calls.length, 2);
assert.equal(buttons[2].attributes['aria-pressed'], 'true');
buttons[3].disabled = true;
click({target: {closest: () => buttons[3]}});
assert.equal(calls.length, 2, 'disabled presets cannot change camera');
click({target: {closest: () => ({dataset:{cameraPreset:'top'}})}});
assert.equal(calls.length, 2, 'unregistered controls cannot change camera');
click({target: {parentElement: {closest: () => buttons[0]}}});
assert.equal(calls.length, 3, 'nested non-element targets still resolve their button');
click({target: null});
assert.equal(calls.length, 3);
canvasListeners.get('pointerdown')();
assert.equal(buttons.every(button => button.attributes['aria-pressed'] === 'false'), true);
controller.apply('top');
canvasListeners.get('wheel')();
assert.equal(buttons.every(button => button.attributes['aria-pressed'] === 'false'), true);
console.log('Camera presets: selection, nested clicks and invalid/disabled inputs passed.');
