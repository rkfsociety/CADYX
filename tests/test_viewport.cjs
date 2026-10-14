const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const events = {};
let drawnPoints = [];
let segments = 0;
let drawnLines = [];
let meshMoves = [];
let drawnLabels = [];
let currentPath = [];
let animationTime = 0;
let renderCalls = 0;
const animationFrames = [];
const context = {
  clearRect() { renderCalls++; drawnPoints = []; segments = 0; drawnLines = []; meshMoves = []; drawnLabels = []; currentPath = []; },
  fillRect(x, y) { drawnPoints.push([x, y]); },
  beginPath() { currentPath = []; }, moveTo(x, y) { currentPath.push([x, y]); },
  lineTo(x, y) { segments++; drawnLines.push([x, y]); currentPath.push([x, y]); },
  closePath() {}, fill() { meshMoves.push(...currentPath); }, stroke() {}, strokeRect() {}, save() {}, restore() {}, setLineDash() {},
  fillText(value) { drawnLabels.push(value); },
};
const canvas = {
  getContext() { return context; },
  getBoundingClientRect() { return { width: 600, height: 400 }; },
  clientHeight: 400,
  classList: { add() {}, remove() {} },
  addEventListener(name, handler) { events[name] = handler; },
  setPointerCapture() {},
};
const sandbox = {
  window: {}, ResizeObserver: class { observe() {} },
  performance: { now: () => animationTime },
  requestAnimationFrame(callback) { animationFrames.push(callback); return animationFrames.length; },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/viewport.js"), "utf8"), sandbox);
const viewer = sandbox.window.CadyxViewport.createViewer(canvas);
const geometry = {
  positions: [0, 0, 0, 0, 0, 1, 1, 0, 0], triangles: [], lines: [0, 1], points: [0, 1],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
viewer.load(geometry);
assert.ok(drawnPoints[1][1] < drawnPoints[0][1], "Positive Z projects upward");
assert.equal(segments, 4, "Sketch lines and the three coordinate axes render in the shared viewport");
assert.deepEqual(drawnLabels.filter((label) => ["X", "Y", "Z"].includes(label)).sort(), ["X", "Y", "Z"]);
const initial = viewer.getCamera();
const initialRenderCalls = renderCalls;
events.pointerdown({ clientX: 100, clientY: 100, pointerId: 1 });
events.pointermove({ clientX: 100, clientY: 120 });
events.pointermove({ clientX: 100, clientY: 130 });
events.pointerup();
const downward = viewer.getCamera();
assert.equal(downward.yaw, initial.yaw);
assert.ok(downward.pitch > initial.pitch, "Dragging downward increases elevation in Z-up coordinates");
assert.equal(animationFrames.length, 1, "A burst of camera events is coalesced into one animation frame");
animationFrames.shift()(animationTime);
assert.equal(renderCalls, initialRenderCalls + 1, "A camera event burst triggers one render, not one render per input event");
events.pointerdown({ clientX: 100, clientY: 130, pointerId: 1 });
events.pointermove({ clientX: 100, clientY: 100 });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(Math.abs(viewer.getCamera().pitch - initial.pitch) < 1e-12, "Reverse vertical drag restores elevation");
events.pointerdown({ clientX: 100, clientY: 100, pointerId: 1 });
events.pointermove({ clientX: 100, clientY: 300 });
events.pointerup();
animationFrames.shift()(animationTime);
assert.equal(viewer.getCamera().pitch, Math.PI / 2, "Dragging can reach the exact top view");
events.wheel({ deltaY: -100, preventDefault() {} });
animationFrames.shift()(animationTime);
const zoomed = viewer.getCamera();
assert.ok(zoomed.zoom > initial.zoom);
viewer.load(geometry, { reset: false });
assert.equal(viewer.getCamera().zoom, zoomed.zoom, "Document refresh preserves the camera");
viewer.setCamera({ yaw: 0, pitch: Math.PI / 2, zoom: 1, projection: "orthographic" });
assert.equal(viewer.getCamera().pitch, Math.PI / 2, "Camera can reach an exact top view");
assert.ok(Math.abs(drawnPoints[1][1] - drawnPoints[0][1]) < 1e-9, "The vertical Z axis points into the camera from above");
assert.throws(() => viewer.setCamera({ yaw: 0, pitch: Math.PI / 2 + 0.01, zoom: 1, projection: "orthographic" }));
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
assert.equal(drawnPoints[1][0], drawnPoints[0][0], "Z stays vertical without horizontal rotation");
assert.throws(() => viewer.setCamera({ yaw: NaN, pitch: 0, zoom: 1, projection: "orthographic" }));
viewer.fit();
assert.equal(viewer.getCamera().pitch, initial.pitch);
assert.equal(viewer.getCamera().zoom, 1);
viewer.load(geometry);
assert.equal(viewer.getCamera().zoom, 1, "New scene resets the camera");
viewer.load(geometry, { reset: false, transition: { type: "sketch-lines", lineRange: [0, 2], durationMs: 100 } });
assert.equal(segments, 3, "A new sketch segment starts as an animated stroke while the coordinate triad remains visible");
animationTime = 50;
animationFrames.shift()(animationTime);
assert.equal(segments, 4);
const midpoint = drawnLines[0];
const endpoint = [
  (drawnPoints[1][0] + drawnPoints[0][0]) / 2 + 2,
  (drawnPoints[1][1] + drawnPoints[0][1]) / 2 + 2,
];
assert.ok(Math.abs(midpoint[0] - endpoint[0]) < 1e-9 && Math.abs(midpoint[1] - endpoint[1]) < 1e-9,
  "A sketch stroke reaches its midpoint with eased progress");
animationTime = 100;
animationFrames.shift()(animationTime);
assert.ok(Math.abs(drawnLines[0][0] - (drawnPoints[1][0] + 2)) < 1e-9
  && Math.abs(drawnLines[0][1] - (drawnPoints[1][1] + 2)) < 1e-9, "The sketch stroke finishes at its endpoint");
const profileBody = {
  positions: [1, 0, 0, 1, 0, 1, 0, 0, 1], triangles: [0, 1, 2], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
animationTime = 200;
viewer.load({ ...profileBody, rotationAxes: [{ start: [0, 0, 0], end: [0, 0, 1] }] }, { reset: false });
assert.ok(drawnLabels.includes("Z · ось вращения"), "Revolved bodies label their global Z rotation axis");
viewer.load(profileBody, { reset: false, transition: { type: "revolve", vertexRange: [0, 3], durationMs: 100 } });
assert.ok(Math.max(...meshMoves.map(([x]) => x)) - Math.min(...meshMoves.map(([x]) => x)) < 1e-9,
  "A revolved profile starts on its axis");
animationTime = 250;
animationFrames.shift()(animationTime);
assert.ok(Math.max(...meshMoves.map(([x]) => x)) - Math.min(...meshMoves.map(([x]) => x)) > 1,
  "A revolved profile grows smoothly away from its axis");
animationTime = 300;
animationFrames.shift()(animationTime);
assert.ok(Math.max(...meshMoves.map(([x]) => x)) - Math.min(...meshMoves.map(([x]) => x)) > 2,
  "The revolved profile reaches its final radius");
const mugOrbit = {
  positions: [0, 0, 5, 20, 0, 5], triangles: [],
  bounds: { min: [0, 0, 0], max: [20, 2, 10] },
  rotationAxes: [{ start: [0, 0, -1], end: [0, 0, 11] }],
};
viewer.load(mugOrbit, { reset: false });
assert.deepEqual(drawnPoints[0], [300, 200], "The revolution axis stays at the camera orbit center despite an offset body bounding box");
assert.ok(drawnPoints[1][0] < 600, "The full asymmetric model remains fitted around its revolution axis");
viewer.setCamera({ yaw: 2.1, pitch: 0.7, zoom: 1, projection: "orthographic" });
assert.deepEqual(drawnPoints[0], [300, 200], "Camera orbit keeps the revolution axis anchored at the viewport center");
console.log("Viewport: Z-up, camera controls, sketch and revolve transitions, zoom, refresh and fit passed.");
