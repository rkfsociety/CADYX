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
const activityUpdate = sandbox.window.CadyxViewport.viewportUpdateForAgentActivity;
const transitionForRevision = sandbox.window.CadyxViewport.transitionForRevision;
const padGrowthFrame = sandbox.window.CadyxViewport.padGrowthFrame;
const padGrowthPosition = sandbox.window.CadyxViewport.padGrowthPosition;
const detailRefreshPlan = sandbox.window.CadyxViewport.detailRefreshPlan;
const initialRefresh = detailRefreshPlan(true, "overview");
assert.equal(Object.values(initialRefresh).filter(Boolean).length, 3,
  "First load requests the activity, dashboard, and comparison snapshots");
assert.equal(2 + Object.values(initialRefresh).filter(Boolean).length, 5);
const ordinaryRefresh = detailRefreshPlan(false, "overview");
assert.deepEqual({ ...ordinaryRefresh }, { activity: false, dashboard: false, comparison: false });
assert.equal(Object.values(ordinaryRefresh).filter(Boolean).length, 0,
  "Ordinary geometry updates reuse SSE-backed auxiliary snapshots");
assert.equal(2 + Object.values(ordinaryRefresh).filter(Boolean).length, 2,
  "Ordinary geometry updates use only project info and scene requests");
const analysisRefresh = detailRefreshPlan(false, "analysis");
assert.deepEqual({ ...analysisRefresh }, { activity: false, dashboard: false, comparison: true },
  "The analysis view still refreshes its comparison snapshot");
const expectedTransition = { expectedRevision: 8, type: "extrude" };
assert.equal(transitionForRevision(expectedTransition, 8), expectedTransition,
  "A command transition is retained for its exact committed revision");
assert.equal(transitionForRevision(expectedTransition, 9), null,
  "A later scene revision never replays an animation for a skipped command");
assert.equal(transitionForRevision(null, 8), null);
const sketchUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "create_sketch", result: { sketch_id: "sketch-1" },
});
assert.equal(sketchUpdate.activeSketchId, "sketch-1");
assert.equal(sketchUpdate.transition, null);
const rectangleUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "add_sketch_rectangle",
  result: { sketch_id: "sketch-1", line_ids: ["line-1", "line-2", "line-3", "line-4"] },
});
assert.equal(rectangleUpdate.transition.allSketchLines, true);
assert.equal(rectangleUpdate.transition.durationMs, 460);
const slotUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "add_sketch_slot_centered",
  result: { sketch_id: "sketch-1", point_ids: ["point-1", "point-2", "point-3", "point-4"],
    line_ids: ["line-1", "line-2"], circle_ids: ["circle-1", "circle-2"] },
});
assert.equal(slotUpdate.activeSketchId, "sketch-1");
assert.equal(slotUpdate.transition.allSketchLines, true,
  "Analytic slot contours animate from the completed activity result");
assert.equal(slotUpdate.transition.durationMs, 930,
  "Slot animation accounts for line strokes and circular arcs");
const arcUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "add_sketch_arc_slot_three_points",
  result: { sketch_id: "sketch-1", line_ids: ["line-1", "line-2"], circle_ids: ["circle-1"] },
});
assert.equal(arcUpdate.transition.type, "sketch-lines",
  "Arc slot commands receive a transition from their actual operation result");
const pointUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "add_sketch_point",
  result: { sketch_id: "sketch-1", point_id: "point-9" },
});
assert.equal(pointUpdate.transition.objectId, "point-9");
assert.equal(pointUpdate.transition.type, "sketch-point");
const objectUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "add_sketch_element", result: { object_id: "circle:1" },
}, "sketch-1");
assert.equal(objectUpdate.activeSketchId, "sketch-1",
  "Element results without a sketch ID retain the active sketch");
assert.equal(objectUpdate.transition.type, "sketch-lines");
const editedSketchUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "update_sketch_elements", result: { object_ids: ["point:1"] },
}, "sketch-1");
assert.equal(editedSketchUpdate.transition.sketchId, "sketch-1",
  "Updates with only changed object IDs continue animating their source sketch");
const padUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "pad_sketch_to_body",
  result: { body_id: "body-1", feature_id: "feature-1" },
});
assert.equal(padUpdate.activeSketchId, null);
assert.equal(padUpdate.transition.type, "pad");
assert.equal(padUpdate.transition.featureId, "feature-1");
const wallPadFrame = padGrowthFrame("XZ", "Y", 50, 10);
assert.deepEqual({ ...wallPadFrame }, {
  growAxis: 1, growOrigin: 50, growClipAxis: 2, growClipOrigin: 10,
}, "An XZ wall pad grows along its Y-normal from the sketch plane");
assert.deepEqual(padGrowthPosition([0, 60, 100], wallPadFrame, 0.5), [0, 55, 100],
  "The wall advances through its thickness instead of rising from the base");
assert.deepEqual(padGrowthPosition([0, 60, 5], wallPadFrame, 0.5), [0, 60, 5],
  "The base plate stays fixed while the wall pad is animated");
assert.deepEqual(padGrowthPosition([0, 60, 10], wallPadFrame, 0.5), [0, 60, 10],
  "The wall to base seam remains fixed on the sketch profile boundary");
const fuseUpdate = activityUpdate("project-1", "project-1", {
  state: "completed", command: "fuse_bodies",
  result: { body_id: "body-2", feature_id: "feature-2" },
});
assert.equal(fuseUpdate.transition.type, "fuse",
  "A standalone Boolean fuse keeps its distinct transition type");
assert.equal(activityUpdate("project-1", "project-2", {
  state: "completed", command: "pad_sketch_to_body",
  result: { body_id: "body-1", feature_id: "feature-1" },
}), null, "Events for another project cannot animate the selected viewer");
const geometry = {
  positions: [0, 0, 0, 0, 0, 1, 1, 0, 0], triangles: [], lines: [0, 1], points: [0, 1],
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
};
viewer.load({ ...geometry, lines: undefined });
assert.ok(drawnPoints[1][1] < drawnPoints[0][1], "Positive Z projects upward");
const projectedOrigin = drawnPoints[0];
const projectedZ = drawnPoints[1];
const fixedLiveGeometry = {
  positions: [-1, 0, 0, 1, 0, 0, 0, 1, 0], triangles: [], lines: undefined,
  bounds: { min: [-1, 0, 0], max: [1, 1, 0] }, orbitCenter: [0, 0, 0], fitRadius: 2,
};
viewer.load(fixedLiveGeometry);
const firstLiveProjection = drawnPoints.map(point => point.slice());
viewer.load({ ...fixedLiveGeometry, bounds: { min: [-100, -40, -10], max: [80, 60, 5] } }, { reset: false });
assert.deepEqual(drawnPoints, firstLiveProjection,
  "Live frame bounds cannot move or rescale the viewport when its orbit center and fit radius are fixed");
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
const dragOrbit = (startX, startY) => {
  viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
  events.pointerdown({ button: 0, clientX: startX, clientY: startY, pointerId: 1 });
  events.pointermove({ clientX: startX + 45, clientY: startY + 25 });
  events.pointerup();
  while (animationFrames.length) animationFrames.shift()(animationTime);
  return viewer.getCamera().orientation;
};
const orbitFromUpperLeft = dragOrbit(60, 60);
const orbitFromLowerRight = dragOrbit(540, 340);
assert.ok(orbitFromUpperLeft.every((value, index) => Math.abs(value - orbitFromLowerRight[index]) < 1e-12),
  "Equal pointer deltas orbit around the model center regardless of where dragging starts");
viewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
const halfTurnDrag = Math.PI / 0.01;
events.pointerdown({ button: 0, clientX: 300, clientY: 210, pointerId: 1 });
events.pointermove({ clientX: 300, clientY: 210 + halfTurnDrag });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation[8] < -0.999,
  "A vertical orbit can turn the camera upside down without a pitch clamp");
events.pointerdown({ button: 0, clientX: 300, clientY: 210 + halfTurnDrag, pointerId: 1 });
events.pointermove({ clientX: 300, clientY: 210 + halfTurnDrag * 2 });
events.pointerup();
animationFrames.shift()(animationTime);
assert.ok(viewer.getCamera().orientation.every((value, index) => Math.abs(value - [1, 0, 0, 0, 1, 0, 0, 0, 1][index]) < 1e-10),
  "Two half-turn orbit drags can complete a full camera revolution");
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
cylinderViewer.setCamera({ yaw: 0.68, pitch: 0.38, zoom: 1.5, projection: "orthographic" });
events.pointerdown({ button: 0, clientX: 300, clientY: 200, pointerId: 3 });
for (const [clientX, clientY] of [[340, 190], [380, 200], [410, 230], [390, 270], [350, 290], [310, 270]]) {
  events.pointermove({ clientX, clientY });
  animationFrames.shift()(animationTime);
  const interiorFragments = strokedPaths.flatMap((path) => {
    const fragments = [];
    for (let index = 0; index + 1 < path.length; index += 2) {
      const [startX, startY] = path[index], [endX, endY] = path[index + 1];
      const midpointY = (startY + endY) / 2;
      const midpointX = (startX + endX) / 2;
      if (midpointX > 90 && midpointX < 270 && midpointY > 85 && midpointY < 155
          && Math.hypot(endX - startX, endY - startY) < 2) {
        fragments.push([startX, startY, endX, endY]);
      }
    }
    return fragments;
  });
  assert.equal(interiorFragments.length, 0,
    `Interactive orbit does not expose isolated subpixel edge fragments (${clientX}, ${clientY})`);
}
events.pointerup();
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
for (const [yaw, pitch] of [[0.3, 0.2], [1.1, -0.35], [2.4, 0.4], [4.2, -0.25]]) {
  explicitEdgeViewer.setCamera({ yaw, pitch, zoom: 1, projection: "orthographic" });
  assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 1,
    `A B-rep boundary stays continuous while orbiting at yaw ${yaw} and pitch ${pitch}`);
}
explicitEdgeViewer.load({
  type: "body",
  positions: [-1, 0, 0, 1, 0, 0, 1, 0, 1, -1, 0, 1, -0.5, -0.015, 0.5, 0.5, -0.015, 0.5],
  triangles: [0, 1, 2, 0, 2, 3], lines: [4, 5], points: [],
  bounds: { min: [-1, -0.015, 0], max: [1, 0, 1] },
});
explicitEdgeViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 1,
  "A sampled B-rep boundary stays visible within the combined mesh and edge tessellation deflection");
explicitEdgeViewer.load({
  type: "body",
  positions: [-1, 0, 0, 1, 0, 0, 1, 0, 1, -1, 0, 1, -0.5, -0.03, 0.5, 0.5, -0.03, 0.5],
  triangles: [0, 1, 2, 0, 2, 3], lines: [4, 5], points: [],
  bounds: { min: [-1, -0.03, 0], max: [1, 0, 1] },
});
explicitEdgeViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: "orthographic" });
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 0,
  "Depth testing still hides a B-rep line farther behind the shaded face than the tessellation tolerance");
const transientEdgeViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: true,
});
transientEdgeViewer.load({
  type: "body",
  positions: [-50, -50, 0, 50, -50, 0, -50, 50, 0, 0, 0, 0, 0.3, 0, 0],
  triangles: [0, 1, 2], lines: [3, 4], points: [],
  bounds: { min: [-50, -50, 0], max: [50, 50, 0] },
});
transientEdgeViewer.setCamera({ yaw: 0, pitch: 0, zoom: 3, projection: "orthographic" });
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 1,
  "A B-rep edge longer than the full-resolution detail threshold remains visible");
events.pointerdown({ button: 0, clientX: 300, clientY: 200, pointerId: 4 });
events.pointermove({ clientX: 320, clientY: 200 });
animationFrames.shift()(animationTime);
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 0,
  "Interactive orbit suppresses isolated short edge marks in its reduced-resolution frame");
events.pointerup();
assert.equal(strokedPaths.reduce((count, path) => count + path.length / 2, 0), 1,
  "The complete B-rep edge returns in the crisp frame when orbit ends");
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
const scanSurfaceViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: true,
});
const scanSurfaceGeometry = {
  positions: [-2, 0, 0, -1, 0, 0, -2, 0, 1, 1, 0, 0, 2, 0, 0, 2, 0, 1],
  triangles: [0, 1, 2, 3, 4, 5], lines: [], points: [],
  bounds: { min: [-2, 0, 0], max: [2, 0, 1] },
};
scanSurfaceViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: 'orthographic' });
scanSurfaceViewer.load(scanSurfaceGeometry);
const cadEdgeSegments = segments;
assert.ok(cadEdgeSegments > 0);
scanSurfaceViewer.load({ ...scanSurfaceGeometry, surfaceTriangleRanges: [{ start: 0, end: 1 }] });
assert.ok(segments > 0 && segments < cadEdgeSegments,
  'Mixed scenes retain CAD edges while suppressing the scan overlay');
scanSurfaceViewer.load({ ...scanSurfaceGeometry, surfaceTriangleRanges: [{ start: 0, end: 2 }] });
assert.equal(segments, 0, 'Shaded scans have no bright boundary or crease overlay');
assert.ok(rasterizedPixels > 0, 'Removing the overlay retains the shaded surface');
scanSurfaceViewer.load(scanSurfaceGeometry);
assert.equal(segments, cadEdgeSegments, 'Scan styling does not mutate the cached CAD edge graph');
scanSurfaceViewer.load({ positions: cylinderPositions, triangles: cylinderTriangles, lines: [], points: [],
  bounds: { min: [-20, -20, 0], max: [20, 20, 40] },
  surfaceTriangleRanges: [{ start: 0, end: cylinderTriangles.length / 3 }] });
assert.equal(segments, 0, 'Scan surfaces also suppress inferred smooth silhouette ink');
const scanDrawingViewer = sandbox.window.CadyxViewport.createViewer(canvas, {
  showCoordinateGizmo: false, showRotationAxes: false, showWorldAxes: false, wireframe: true, shaded: false,
});
scanDrawingViewer.setCamera({ yaw: 0, pitch: 0, zoom: 1, projection: 'orthographic' });
scanDrawingViewer.load({ ...scanSurfaceGeometry, surfaceTriangleRanges: [{ start: 0, end: 2 }] });
assert.ok(segments > 0, 'Orthographic wireframe scans remain visible');
console.log("Viewport: camera, geometry, transitions, depth-tested edges and scan surface styling passed.");

