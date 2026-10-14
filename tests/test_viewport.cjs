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
let strokedPaths = [];
let animationTime = 0;
let renderCalls = 0;
const animationFrames = [];
const context = {
  clearRect() { renderCalls++; drawnPoints = []; segments = 0; drawnLines = []; meshMoves = []; drawnLabels = []; currentPath = []; strokedPaths = []; },
  fillRect(x, y) { drawnPoints.push([x, y]); },
  beginPath() { currentPath = []; }, moveTo(x, y) { currentPath.push([x, y]); },
  lineTo(x, y) { segments++; drawnLines.push([x, y]); currentPath.push([x, y]); },
  closePath() {}, fill() { meshMoves.push(...currentPath); }, stroke() { strokedPaths.push(currentPath.slice()); }, strokeRect() {}, save() {}, restore() {}, setLineDash() {},
  fillText(value) { drawnLabels.push(value); },
};
const canvas = {
  getContext() { return context; },
  getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 400 }; },
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
const isRotationMatrix = (matrix) => {
  for (let row = 0; row < 3; row += 1) {
    for (let other = 0; other < 3; other += 1) {
      const dot = matrix[row * 3] * matrix[other * 3]
        + matrix[row * 3 + 1] * matrix[other * 3 + 1]
        + matrix[row * 3 + 2] * matrix[other * 3 + 2];
      if (Math.abs(dot - (row === other ? 1 : 0)) > 1e-8) return false;
    }
  }
  return true;
};
const initialRenderCalls = renderCalls;
events.pointerdown({ clientX: 300, clientY: 200, pointerId: 1 });
events.pointermove({ clientX: 300, clientY: 220 });
events.pointermove({ clientX: 300, clientY: 230 });
events.pointerup();
const downward = viewer.getCamera();
assert.notDeepEqual(downward.orientation, initial.orientation, "Dragging rotates the camera");
assert.ok(isRotationMatrix(downward.orientation), "Drag keeps the full camera orientation orthonormal");
assert.equal(animationFrames.length, 1, "A burst of camera events is coalesced into one animation frame");
animationFrames.shift()(animationTime);
assert.equal(renderCalls, initialRenderCalls + 1, "A camera event burst triggers one render, not one render per input event");
events.pointerdown({ clientX: 300, clientY: 230, pointerId: 1 });
events.pointermove({ clientX: 300, clientY: 220 });
events.pointermove({ clientX: 300, clientY: 200 });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - initial.orientation[index]) < 1e-12),
  "Reverse drag restores the full camera orientation");
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
events.pointerdown({ clientX: 300, clientY: 200, pointerId: 1 });
for (let y = 210; y <= 400; y += 10) events.pointermove({ clientX: 300, clientY: y });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation[8] < -0.999,
  "Trackball drag passes through the former pitch limit and turns the camera upside down");
events.pointerdown({ clientX: 300, clientY: 400, pointerId: 1 });
for (let y = 410; y <= 600; y += 10) events.pointermove({ clientX: 300, clientY: y });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - [1, 0, 0, 0, 1, 0, 0, 0, 1][index]) < 1e-10),
  "A continued drag can complete a full camera revolution");
events.wheel({ deltaY: -100, preventDefault() {} });
animationFrames.shift()(animationTime);
const zoomed = viewer.getCamera();
assert.ok(zoomed.zoom > initial.zoom);
viewer.load(geometry, { reset: false });
assert.equal(viewer.getCamera().zoom, zoomed.zoom, "Document refresh preserves the camera");
viewer.setCamera({ yaw: 0, pitch: Math.PI / 2, zoom: 1, projection: "orthographic" });
assert.ok(Math.abs(viewer.getCamera().orientation[8]) < 1e-12, "Camera can reach an exact top view");
assert.ok(Math.abs(drawnPoints[1][1] - drawnPoints[0][1]) < 1e-9, "The vertical Z axis points into the camera from above");
viewer.setCamera({ yaw: 0, pitch: Math.PI * 1.5, zoom: 1, projection: "orthographic" });
assert.ok(isRotationMatrix(viewer.getCamera().orientation), "Camera accepts orientations beyond the former pitch limit");
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
assert.equal(drawnPoints[1][0], drawnPoints[0][0], "Z stays vertical without horizontal rotation");
assert.throws(() => viewer.setCamera({ yaw: NaN, pitch: 0, zoom: 1, projection: "orthographic" }));
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
viewer.orbitBy(0, Math.PI / 2, 1);
viewer.orbitBy(0, Math.PI / 2, 1);
assert.ok(viewer.getCamera().orientation[8] < -0.999, "API camera orbits can accumulate beyond ninety degrees");
assert.throws(() => viewer.setCamera({ orientation: [1, 0, 0, 0, 1, 0, 0, 0, -1], zoom: 1, projection: "orthographic" }));
viewer.fit();
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - initial.orientation[index]) < 1e-12));
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
const cylinderSegments = 64;
const cylinderPositions = [];
const cylinderTriangles = [];
for (const z of [0, 40]) {
  for (let index = 0; index < cylinderSegments; index += 1) {
    const angle = index * Math.PI * 2 / cylinderSegments;
    cylinderPositions.push(20 * Math.cos(angle), 20 * Math.sin(angle), z);
  }
}
for (let index = 0; index < cylinderSegments; index += 1) {
  const next = (index + 1) % cylinderSegments;
  const lower = index, lowerNext = next, upper = index + cylinderSegments, upperNext = next + cylinderSegments;
  cylinderTriangles.push(lower, lowerNext, upperNext, lower, upperNext, upper);
}
const cylinderViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, wireframe: true, shaded: false,
});
cylinderViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
cylinderViewer.load({
  positions: cylinderPositions, triangles: cylinderTriangles, lines: [], points: [],
  bounds: { min: [-20, -20, 0], max: [20, 20, 40] },
});
const verticalContourXs = new Set();
for (const path of strokedPaths) {
  for (let index = 0; index + 1 < path.length; index += 2) {
    const [startX, startY] = path[index], [endX, endY] = path[index + 1];
    if (Math.abs(startX - endX) < 1e-8 && Math.abs(startY - endY) > 20) verticalContourXs.add(Math.round(startX));
  }
}
assert.ok(verticalContourXs.has(178) && verticalContourXs.has(422),
  "Front orthographic view draws both smooth vertical cylinder silhouettes");
cylinderViewer.setCamera({ yaw: Math.PI / 2, pitch: 0, zoom: 1, projection: "orthographic" });
const sideContourXs = new Set();
for (const path of strokedPaths) {
  for (let index = 0; index + 1 < path.length; index += 2) {
    const [startX, startY] = path[index], [endX, endY] = path[index + 1];
    if (Math.abs(startX - endX) < 1e-8 && Math.abs(startY - endY) > 20) sideContourXs.add(Math.round(startX));
  }
}
assert.ok(sideContourXs.has(178) && sideContourXs.has(422),
  "Side orthographic view draws both smooth vertical cylinder silhouettes");
const sweepPath = Array.from({ length: 512 }, (_, index) => ({ x: index / 511, y: 0 }));
viewer.load({
  positions: [0, 0, 0, 0.5, 0, 1, 0.5, 0, 0, 1, 0, 1],
  triangles: [0, 1, 2, 2, 1, 3], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 0, 1] },
}, { reset: false, transition: { type: "sweep", path: sweepPath, plane: "XY", durationMs: 100 } });
animationTime = 350;
animationFrames.shift()(animationTime);
assert.equal(meshMoves.length, 3, "A long sweep path reveals only its first mesh portion at half progress");
animationTime = 400;
animationFrames.shift()(animationTime);
assert.equal(meshMoves.length, 6, "The sweep transition reveals the complete mesh when finished");
const threadBody = {
  positions: [0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1],
  triangles: [0, 1, 2, 2, 1, 3], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
animationTime = 450;
viewer.load(threadBody, { reset: false, transition: { type: "thread", vertexRange: [0, 4], durationMs: 100 } });
const threadStartHeight = Math.max(...meshMoves.map(([, y]) => y)) - Math.min(...meshMoves.map(([, y]) => y));
animationTime = 500;
animationFrames.shift()(animationTime);
const threadMidHeight = Math.max(...meshMoves.map(([, y]) => y)) - Math.min(...meshMoves.map(([, y]) => y));
animationTime = 550;
animationFrames.shift()(animationTime);
const threadEndHeight = Math.max(...meshMoves.map(([, y]) => y)) - Math.min(...meshMoves.map(([, y]) => y));
assert.ok(threadMidHeight > threadStartHeight, "A metric thread grows along Z during its transition");
assert.ok(threadEndHeight > threadMidHeight, "A metric thread transition reaches its full length");
console.log("Viewport: Z-up, camera controls, sketch, revolve, sweep and thread transitions, zoom, refresh and fit passed.");

