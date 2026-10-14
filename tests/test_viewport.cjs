const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const events = {};
let drawnPoints = [];
let segments = 0;
const context = {
  clearRect() { drawnPoints = []; segments = 0; },
  fillRect(x, y) { drawnPoints.push([x, y]); },
  beginPath() {}, moveTo() {}, lineTo() { segments++; }, closePath() {}, fill() {}, stroke() {},
};
const canvas = {
  getContext() { return context; },
  getBoundingClientRect() { return { width: 600, height: 400 }; },
  clientHeight: 400,
  classList: { add() {}, remove() {} },
  addEventListener(name, handler) { events[name] = handler; },
  setPointerCapture() {},
};
const sandbox = { window: {}, ResizeObserver: class { observe() {} } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/viewport.js"), "utf8"), sandbox);
const viewer = sandbox.window.CadyxViewport.createViewer(canvas);
const geometry = {
  positions: [0, 0, 0, 0, 0, 1, 1, 0, 0], triangles: [], lines: [0, 1], points: [0, 1],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
viewer.load(geometry);
assert.ok(drawnPoints[1][1] < drawnPoints[0][1], "Positive Z projects upward");
assert.equal(segments, 1, "Sketch lines render in the shared viewport");
const initial = viewer.getCamera();
events.pointerdown({ clientX: 100, clientY: 100, pointerId: 1 });
events.pointermove({ clientX: 100, clientY: 120 });
events.pointerup();
const downward = viewer.getCamera();
assert.equal(downward.yaw, initial.yaw);
assert.ok(downward.pitch > initial.pitch, "Dragging downward increases elevation in Z-up coordinates");
events.pointerdown({ clientX: 100, clientY: 120, pointerId: 1 });
events.pointermove({ clientX: 100, clientY: 100 });
events.pointerup();
assert.ok(Math.abs(viewer.getCamera().pitch - initial.pitch) < 1e-12, "Reverse vertical drag restores elevation");
events.wheel({ deltaY: -100, preventDefault() {} });
const zoomed = viewer.getCamera();
assert.ok(zoomed.zoom > initial.zoom);
viewer.load(geometry, { reset: false });
assert.equal(viewer.getCamera().zoom, zoomed.zoom, "Document refresh preserves the camera");
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
assert.equal(drawnPoints[1][0], drawnPoints[0][0], "Z stays vertical without horizontal rotation");
assert.throws(() => viewer.setCamera({ yaw: NaN, pitch: 0, zoom: 1, projection: "orthographic" }));
viewer.fit();
assert.equal(viewer.getCamera().pitch, initial.pitch);
assert.equal(viewer.getCamera().zoom, 1);
viewer.load(geometry);
assert.equal(viewer.getCamera().zoom, 1, "New scene resets the camera");
console.log("Viewport: Z-up, vertical orbit, camera validation, zoom, refresh and fit passed.");
