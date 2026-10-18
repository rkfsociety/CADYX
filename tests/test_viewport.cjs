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
let dashPatterns = [];
let arcs = 0;
let animationTime = 0;
let renderCalls = 0;
let rasterizedPixels = 0;
let rasterizedBounds = null;
const animationFrames = [];
const context = {
  clearRect() {
    renderCalls++;
    drawnPoints = []; segments = 0; drawnLines = []; meshMoves = []; drawnLabels = [];
    currentPath = []; strokedPaths = []; dashPatterns = []; arcs = 0;
    rasterizedPixels = 0; rasterizedBounds = null;
  },
  fillRect(x, y, width, height) {
    // Capture geometry point markers, not coordinate-label backgrounds.
    if (width > 0 && height > 0 && width <= 4 && height <= 4) drawnPoints.push([x, y]);
  },
  beginPath() { currentPath = []; }, moveTo(x, y) { currentPath.push([x, y]); },
  lineTo(x, y) { segments++; drawnLines.push([x, y]); currentPath.push([x, y]); },
  closePath() {}, fill() { meshMoves.push(...currentPath); }, stroke() { strokedPaths.push(currentPath.slice()); }, strokeRect() {}, save() {}, restore() {},
  setLineDash(value) { dashPatterns.push(value.slice()); }, arc() { arcs++; },
  fillText(value) { drawnLabels.push(value); },
  measureText(value) { return { width: String(value).length * 7 }; },
  createImageData(width, height) { return { width, height, data: new Uint8ClampedArray(width * height * 4) }; },
  putImageData(image) {
    let minX = image.width, maxX = -1, minY = image.height, maxY = -1;
    for (let y = 0; y < image.height; y += 1) {
      for (let x = 0; x < image.width; x += 1) {
        if (!image.data[(y * image.width + x) * 4 + 3]) continue;
        rasterizedPixels += 1;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
    }
    rasterizedBounds = rasterizedPixels ? { minX, maxX, minY, maxY } : null;
  },
  drawImage() {},
};
const canvas = {
  getContext() { return context; },
  getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 400 }; },
  classList: { add() {}, remove() {} },
  style: { cursor: "", removeProperty(property) { this[property] = ""; } },
  addEventListener(name, handler) { events[name] = handler; },
  setPointerCapture() {},
};
const sandbox = {
  window: {}, ResizeObserver: class { observe() {} },
  document: { createElement() { return { width: 0, height: 0, getContext() { return context; } }; } },
  performance: { now: () => animationTime },
  requestAnimationFrame(callback) { animationFrames.push(callback); return animationFrames.length; },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/viewport.js"), "utf8"), sandbox);
const viewer = sandbox.window.CadyxViewport.createViewer(canvas, { showCoordinateGizmo: false });
const geometry = {
  positions: [0, 0, 0, 0, 0, 1, 1, 0, 0], triangles: [], lines: [0, 1], points: [0, 1],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
viewer.load({ ...geometry, lines: undefined });
assert.ok(drawnPoints[1][1] < drawnPoints[0][1], "Positive Z projects upward");
const projectedOrigin = drawnPoints[0];
const projectedZ = drawnPoints[1];
viewer.load(geometry);
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
events.pointerdown({ button: 0, clientX: 300, clientY: 200, pointerId: 1 });
assert.equal(canvas.style.cursor, "grabbing", "Drag sets the grabbing cursor");
events.pointermove({ clientX: 300, clientY: 220 });
events.pointermove({ clientX: 300, clientY: 230 });
assert.equal(animationFrames.length, 1, "A burst of camera events is coalesced into one animation frame");
animationFrames.shift()(animationTime);
assert.equal(renderCalls, initialRenderCalls + 1, "A camera event burst triggers one render, not one render per input event");
assert.equal(canvas.width, 360, "Interactive orbit reduces the backing width while dragging");
assert.equal(canvas.height, 240, "Interactive orbit reduces the backing height while dragging");
assert.equal(canvas.width * canvas.height, 600 * 400 * 0.36,
  "Interactive orbit rasterizes 36% as many pixels as the full-resolution viewport");
const interactiveRenderCalls = renderCalls;
events.pointerup();
assert.equal(canvas.width, 600, "Releasing the pointer restores full-resolution width");
assert.equal(canvas.height, 400, "Releasing the pointer restores full-resolution height");
assert.equal(renderCalls, interactiveRenderCalls + 1, "Pointer release renders one crisp full-resolution frame");
assert.equal(canvas.style.cursor, "", "Releasing the pointer restores the CSS cursor");
const downward = viewer.getCamera();
assert.notDeepEqual(downward.orientation, initial.orientation, "Dragging rotates the camera");
assert.ok(isRotationMatrix(downward.orientation), "Drag keeps the full camera orientation orthonormal");
events.pointerdown({ button: 0, clientX: 300, clientY: 230, pointerId: 1 });
events.pointermove({ clientX: 300, clientY: 220 });
events.pointermove({ clientX: 300, clientY: 200 });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - initial.orientation[index]) < 1e-12),
  "Reverse drag restores the full camera orientation");
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
events.pointerdown({ button: 0, clientX: 300, clientY: 200, pointerId: 1 });
for (let y = 210; y <= 400; y += 10) events.pointermove({ clientX: 300, clientY: y });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation[8] < -0.999,
  "Trackball drag passes through the former pitch limit and turns the camera upside down");
events.pointerdown({ button: 0, clientX: 300, clientY: 400, pointerId: 1 });
for (let y = 410; y <= 600; y += 10) events.pointermove({ clientX: 300, clientY: y });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - [1, 0, 0, 0, 1, 0, 0, 0, 1][index]) < 1e-10),
  "A continued drag can complete a full camera revolution");
events.wheel({ deltaY: -100, preventDefault() {} });
animationFrames.shift()(animationTime);
const zoomed = viewer.getCamera();
assert.ok(zoomed.zoom > initial.zoom);
viewer.load({ ...geometry, lines: undefined }, { reset: false });
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
const midpoint = drawnLines.at(-1);
const endpoint = [
  (projectedZ[0] + projectedOrigin[0]) / 2,
  (projectedZ[1] + projectedOrigin[1]) / 2,
];
assert.ok(Math.abs(midpoint[0] - endpoint[0]) < 1e-9 && Math.abs(midpoint[1] - endpoint[1]) < 1e-9,
  "A sketch stroke reaches its midpoint with eased progress");
animationTime = 100;
animationFrames.shift()(animationTime);
assert.ok(Math.abs(drawnLines.at(-1)[0] - projectedZ[0]) < 1e-9
  && Math.abs(drawnLines.at(-1)[1] - projectedZ[1]) < 1e-9, "The sketch stroke finishes at its endpoint");
viewer.load({
  positions: [0, 0, 0, 1, 1, 0], triangles: [], lines: [0, 1], points: [], line_modes: ["infinite"],
  bounds: { min: [0, 0, 0], max: [1, 1, 0] },
}, { reset: false });
const infiniteLine = strokedPaths.at(-1);
assert.ok(Math.hypot(infiniteLine[1][0] - infiniteLine[0][0],
  infiniteLine[1][1] - infiniteLine[0][1]) > 600, "Infinite sketch lines span the viewport");
viewer.load({
  positions: [0, 0, 0, 1, 1, 0], triangles: [], lines: [0, 1], points: [0],
  construction: true, line_modes: ["segment"], bounds: { min: [0, 0, 0], max: [1, 1, 0] },
}, { reset: false });
assert.ok(dashPatterns.some((pattern) => pattern.join(",") === "6,5"), "Construction lines are dashed");
assert.ok(arcs > 0, "Construction points use a visible hollow marker");
const profileBody = {
  positions: [1, 0, 0, 1, 0, 1, 0, 0, 1], triangles: [0, 1, 2], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
animationTime = 200;
viewer.load({ ...profileBody, rotationAxes: [{ start: [0, 0, 0], end: [0, 0, 1] }] }, { reset: false });
assert.ok(drawnLabels.includes("Z · ось вращения"), "Revolved bodies label their global Z rotation axis");
viewer.load(profileBody, { reset: false, transition: { type: "revolve", vertexRange: [0, 3], durationMs: 100 } });
assert.ok(!rasterizedBounds || rasterizedBounds.maxX - rasterizedBounds.minX <= 1,
  "A revolved profile starts on its axis");
animationTime = 250;
animationFrames.shift()(animationTime);
assert.ok(rasterizedBounds && rasterizedBounds.maxX - rasterizedBounds.minX > 1,
  "A revolved profile grows smoothly away from its axis");
animationTime = 300;
animationFrames.shift()(animationTime);
assert.ok(rasterizedBounds && rasterizedBounds.maxX - rasterizedBounds.minX > 2,
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
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: true,
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
for (const [yaw, pitch] of [[0.17, 0.2], [0.73, -0.35], [1.41, 0.4], [2.62, -0.25], [4.19, 0.3]]) {
  cylinderViewer.setCamera({ yaw, pitch, zoom: 1, projection: "orthographic" });
  const verticalSilhouetteSpans = [];
  for (const path of strokedPaths) {
    for (let index = 0; index + 1 < path.length; index += 2) {
      const [startX, startY] = path[index], [endX, endY] = path[index + 1];
      if (Math.abs(startX - endX) < 1e-7 && Math.abs(endY - startY) > 200) {
        verticalSilhouetteSpans.push(Math.abs(endY - startY));
      }
    }
  }
  assert.ok(verticalSilhouetteSpans.length >= 2,
    `Depth-tested cylinder silhouettes remain continuous at yaw ${yaw} and pitch ${pitch}`);
  assert.ok(verticalSilhouetteSpans.every((span) => span > 200),
    `Each visible cylinder side reaches from top to bottom at yaw ${yaw} and pitch ${pitch}`);
}
const splitSquareViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false,
});
splitSquareViewer.load({
  positions: [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0],
  triangles: [0, 1, 2, 3, 4, 5], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 0] },
});
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 4,
  "Coincident mesh vertices share adjacency so a coplanar triangulation diagonal is hidden");
splitSquareViewer.load({
  positions: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0],
  triangles: [0, 1, 2, 3, 4, 5], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
});
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 5,
  "Coincident vertices preserve a real crease between perpendicular faces");
const explicitEdgeViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: true,
});
explicitEdgeViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
explicitEdgeViewer.load({
  type: "body",
  positions: [-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0],
  triangles: [0, 1, 2, 0, 2, 3], lines: [0, 1], points: [],
  bounds: { min: [-1, -1, 0], max: [1, 1, 0] },
});
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 1,
  "A depth-tested B-rep edge stays continuous along the boundary of its own shaded face");
const facetedCylinderViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false,
});
const facetedCylinderPositions = [], facetedCylinderTriangles = [];
function addFacetedCylinderTriangle(a, b, c) {
  const start = facetedCylinderPositions.length / 3;
  facetedCylinderPositions.push(...a, ...b, ...c);
  facetedCylinderTriangles.push(start, start + 1, start + 2);
}
const facetedCylinderSegments = 16;
for (let index = 0; index < facetedCylinderSegments; index += 1) {
  const angle = index * Math.PI * 2 / facetedCylinderSegments;
  const nextAngle = (index + 1) * Math.PI * 2 / facetedCylinderSegments;
  const lower = (theta) => [Math.cos(theta), Math.sin(theta), 0];
  const upper = (theta) => [Math.cos(theta), Math.sin(theta), 2];
  addFacetedCylinderTriangle(lower(angle), lower(nextAngle), upper(nextAngle));
  addFacetedCylinderTriangle(lower(angle), upper(nextAngle), upper(angle));
}
facetedCylinderViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
facetedCylinderViewer.load({
  positions: facetedCylinderPositions, triangles: facetedCylinderTriangles, lines: [], points: [],
  bounds: { min: [-1, -1, 0], max: [1, 1, 2] },
});
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 36,
  "A coarse smooth cylinder keeps its silhouette and end boundaries without tessellation seams");
const hiddenEdgeViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true,
});
hiddenEdgeViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
hiddenEdgeViewer.load({
  positions: [-1, -1, 0, 1, -1, 0, 0, -1, 2, -1, 1, 0, 1, 1, 0, 0, 1, 2],
  triangles: [0, 1, 2, 3, 4, 5], lines: [], points: [],
  bounds: { min: [-1, -1, 0], max: [1, 1, 2] },
}, { reset: false });
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 3,
  "Edges on a fully occluded rear face are hidden behind the front face");
const sweepPath = Array.from({ length: 512 }, (_, index) => ({ x: index / 511, y: 0 }));
viewer.load({
  positions: [0, 0, 0, 0.5, 0, 1, 0.5, 0, 0, 1, 0, 1],
  triangles: [0, 1, 2, 2, 1, 3], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 0, 1] },
}, { reset: false, transition: { type: "sweep", path: sweepPath, plane: "XY", durationMs: 100 } });
animationTime = 350;
animationFrames.shift()(animationTime);
const sweepMidPixels = rasterizedPixels;
assert.ok(sweepMidPixels > 0, "A long sweep path reveals an initial mesh portion at half progress");
animationTime = 400;
animationFrames.shift()(animationTime);
assert.ok(rasterizedPixels > sweepMidPixels, "The sweep transition reveals more mesh when finished");
const threadBody = {
  positions: [0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1],
  triangles: [0, 1, 2, 2, 1, 3], lines: [], points: [],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
animationTime = 450;
viewer.load(threadBody, { reset: false, transition: { type: "thread", vertexRange: [0, 4], durationMs: 100 } });
const threadStartHeight = rasterizedBounds ? rasterizedBounds.maxY - rasterizedBounds.minY : 0;
animationTime = 500;
animationFrames.shift()(animationTime);
const threadMidHeight = rasterizedBounds ? rasterizedBounds.maxY - rasterizedBounds.minY : 0;
animationTime = 550;
animationFrames.shift()(animationTime);
const threadEndHeight = rasterizedBounds ? rasterizedBounds.maxY - rasterizedBounds.minY : 0;
assert.ok(threadMidHeight > threadStartHeight, "A metric thread grows along Z during its transition");
assert.ok(threadEndHeight > threadMidHeight, "A metric thread transition reaches its full length");
const motionViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false,
});
motionViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
motionViewer.load({
  positions: [2, 0, 0, 3, 0, 0, 2, 0, 1], triangles: [0, 1, 2], lines: [], points: [],
  bounds: { min: [2, 0, 0], max: [3, 0, 1] },
  bodyTriangleRanges: [{ objectId: "body:planet", start: 0, end: 1 }],
});
const motionBoundsBefore = rasterizedBounds;
const motionResult = motionViewer.animateBodyMotion([{
  body_id: "body:planet", center_x: 2, center_y: 0,
  pivot_x: 2, pivot_y: 0,
  spin_speed_radians_per_second: Math.PI,
  orbit_speed_radians_per_second: 0,
}], 1000);
assert.equal(motionResult.body_count, 1);
animationTime = 1050;
animationFrames.pop()(animationTime);
assert.notDeepEqual(rasterizedBounds, motionBoundsBefore,
  "A revision-neutral body motion rotates its rendered triangles about the requested pivot");
animationTime = 1200;
animationFrames.pop()(animationTime);
assert.notDeepEqual(rasterizedBounds, motionBoundsBefore,
  "Gear motion remains animated across frames before its bounded duration ends");
animationTime = 1600;
animationFrames.pop()(animationTime);
assert.deepEqual(rasterizedBounds, motionBoundsBefore,
  "Temporary gear motion restores the saved viewport pose after its bounded duration");
console.log("Viewport: Z-up, camera controls, sketch, solid transitions, temporary body motion, zoom, refresh and fit passed.");

