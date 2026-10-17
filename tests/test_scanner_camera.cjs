const assert = require("node:assert/strict");
const {
  rotateScannerPoint,
  normalizeTraceSweepDegrees,
  traceArcFromPoints,
  traceSegmentEnd,
  traceSegmentStart,
  traceIsClosed,
  createTraceDraft,
} = require("../web/scanner-camera.js");

const closeTo = (actual, expected, message) => {
  assert.ok(Math.abs(actual - expected) < 1e-10, `${message}: expected ${expected}, got ${actual}`);
};

assert.deepEqual(rotateScannerPoint(2, -3, 4, 0, 0), [2, -3, 4], "zero orbit preserves camera-space coordinates");

const yawed = rotateScannerPoint(0, 1, 0, Math.PI / 2, 0);
closeTo(yawed[0], 0, "yaw leaves the vertical axis horizontal coordinate unchanged");
closeTo(yawed[1], 1, "yaw preserves the vertical axis");
closeTo(yawed[2], 0, "yaw leaves a vertical-axis point on the vertical axis");

const horizontal = rotateScannerPoint(0, 0, 1, Math.PI / 2, 0);
closeTo(horizontal[0], -1, "yaw rotates depth into horizontal view");
closeTo(horizontal[1], 0, "yaw does not roll depth into vertical view");

const pitched = rotateScannerPoint(0, 0, 1, 0, Math.PI / 2);
closeTo(pitched[0], 0, "pitch leaves the horizontal coordinate unchanged");
closeTo(pitched[1], -1, "pitch rotates depth around the horizontal axis");

assert.equal(normalizeTraceSweepDegrees(270), -90);
assert.equal(normalizeTraceSweepDegrees(-270), 90);
const arc = traceArcFromPoints({ x: 10, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 10 });
closeTo(arc.radius_pixels, 10, "arc keeps radius in source pixels");
closeTo(arc.start_angle_degrees, 0, "arc start angle comes from source geometry");
closeTo(arc.sweep_degrees, 90, "arc uses the shortest signed sweep");
const arcEnd = traceSegmentEnd(arc);
closeTo(arcEnd.x, 0, "arc end x is recomputed from exact radius");
closeTo(arcEnd.y, 10, "arc end y is recomputed from exact radius");
const arcStart = traceSegmentStart(arc);
closeTo(arcStart.x, 10, "arc start x is exact");
closeTo(arcStart.y, 0, "arc start y is exact");

const closed = [
  { kind: "line", start: { x: 0, y: 0 }, end: { x: 10, y: 0 } },
  { kind: "line", start: { x: 10, y: 0 }, end: { x: 0, y: 0 } },
];
assert.equal(traceIsClosed(closed), true, "closed trace is recognized from endpoints");
assert.equal(traceIsClosed(closed.slice(0, 1)), false, "open trace remains open");

const draft = createTraceDraft();
draft.update(segments => segments.push(closed[0]));
assert.equal(draft.canUndo, true);
draft.undo();
assert.equal(draft.snapshot().length, 0);
assert.equal(draft.canRedo, true);
draft.redo();
assert.equal(draft.snapshot().length, 1);
draft.update(segments => segments.push(closed[1]));
assert.equal(draft.canRedo, false, "new edit clears redo history");

console.log("Scanner camera and constrained traced-curve geometry tests passed.");
