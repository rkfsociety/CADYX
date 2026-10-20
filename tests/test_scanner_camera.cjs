const assert = require("node:assert/strict");
const {
  rotateScannerPoint,
  scannerOrbitDelta,
  scannerDepthOrder,
  scannerPreviewPoint,
  scannerReprojectDepthPixel,
  scannerSupportColor,
  normalizeTraceSweepDegrees,
  traceArcFromPoints,
  traceSegmentGeometry,
  traceSegmentEnd,
  traceSegmentStart,
  traceIsClosed,
  createTraceDraft,
} = require("../web/scanner-camera.js");

const closeTo = (actual, expected, message) => {
  assert.ok(Math.abs(actual - expected) < 1e-10, `${message}: expected ${expected}, got ${actual}`);
};

assert.deepEqual(rotateScannerPoint(2, -3, 4, 0, 0), [2, -3, 4], "zero orbit preserves camera-space coordinates");
assert.deepEqual(scannerOrbitDelta(10, 10), { yaw: -0.08, pitch: -0.08 }, "dragging right or down reverses the previous orbit direction");
assert.deepEqual(scannerOrbitDelta(-10, -10), { yaw: 0.08, pitch: 0.08 }, "dragging left or up reverses the previous orbit direction");

const yawed = rotateScannerPoint(0, 1, 0, Math.PI / 2, 0);
closeTo(yawed[0], 0, "yaw leaves the vertical axis horizontal coordinate unchanged");
closeTo(yawed[1], 1, "yaw preserves the vertical axis");
closeTo(yawed[2], 0, "yaw leaves a vertical-axis point on the vertical axis");

const horizontal = rotateScannerPoint(0, 0, 1, Math.PI / 2, 0);
closeTo(horizontal[0], -1, "yaw rotates depth into horizontal view");
closeTo(horizontal[1], 0, "yaw does not roll depth into vertical view");

assert.deepEqual(scannerPreviewPoint(2, -3, 4, 0, 0), [-2, 3, 4], "preview flips the image plane by 180 degrees without changing depth");
assert.deepEqual(scannerDepthOrder([[0, 0, -150], [0, 0, -200], [0, 0, -175]]), [1, 2, 0], "cloud points are drawn far-to-near so foreground points occlude the rear during orbit");
const rotatedPreview = scannerPreviewPoint(2, -3, 4, 0.4, -0.2);
const rotatedPoint = rotateScannerPoint(2, -3, 4, 0.4, -0.2);
assert.deepEqual(rotatedPreview, [-rotatedPoint[0], -rotatedPoint[1], rotatedPoint[2]], "preview-only rotation leaves camera depth unchanged");

const qMatrix = [
  1, 0, 0, -52.766502,
  0, 1, 0, -199.493652,
  0, 0, 0, 891.2453,
  0, 0, 0.011310811, 5.890967,
];
const depthPoint = scannerReprojectDepthPixel(153, 299, 25000, qMatrix, 0.005);
assert.ok(depthPoint, "positive Revo depth maps to a point");
closeTo(depthPoint[2], -125, "Q ray is scaled to the measured raw depth in millimeters");
assert.ok(depthPoint[0] > 10, "horizontal pixel offset is projected along the camera ray");
assert.ok(depthPoint[1] < 0, "scanner camera Y is inverted into viewport-up coordinates");
assert.equal(scannerReprojectDepthPixel(153, 299, 0, qMatrix, 0.005), null, "zero depth remains invalid");
assert.equal(scannerReprojectDepthPixel(153, 299, 1, qMatrix, 0.005), null, "near-zero invalid readings are filtered");

assert.equal(scannerSupportColor(1, 4), "hsl(25 88% 58%)", "one-frame points use the low-support color");
assert.equal(scannerSupportColor(2, 4), "hsl(174 88% 58%)", "two-frame points use the medium-support color");
assert.equal(scannerSupportColor(4, 4), "hsl(275 88% 58%)", "maximum-support points use the high-support color");
assert.equal(scannerSupportColor(12, 4), "hsl(275 88% 58%)", "support colors clamp to the file's maximum");
assert.equal(scannerSupportColor(0, 4), "hsl(0 0% 55%)", "invalid support is visibly neutral");

const pitched = rotateScannerPoint(0, 0, 1, 0, Math.PI / 2);
closeTo(pitched[0], 0, "pitch leaves the horizontal coordinate unchanged");
closeTo(pitched[1], -1, "pitch rotates depth around the horizontal axis");

assert.equal(normalizeTraceSweepDegrees(270), -90);
assert.equal(normalizeTraceSweepDegrees(-270), 90);
const arc = traceArcFromPoints({ x: 10, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 10 });
closeTo(arc.radius_pixels, 10, "arc keeps radius in source pixels");
closeTo(arc.start_angle_degrees, 0, "arc start angle comes from source geometry");
closeTo(arc.sweep_degrees, 90, "arc uses the shortest signed sweep");
const arcGeometry = traceSegmentGeometry(arc);
assert.strictEqual(traceSegmentGeometry(arc), arcGeometry, "derived arc geometry is cached for the unchanged source segment");
closeTo(arcGeometry.startRadians, 0, "render cache keeps the exact start angle in radians");
closeTo(arcGeometry.endRadians, Math.PI / 2, "render cache keeps the exact end angle in radians");
const arcEnd = traceSegmentEnd(arc);
closeTo(arcEnd.x, 0, "arc end x is recomputed from exact radius");
closeTo(arcEnd.y, 10, "arc end y is recomputed from exact radius");
const arcStart = traceSegmentStart(arc);
closeTo(arcStart.x, 10, "arc start x is exact");
closeTo(arcStart.y, 0, "arc start y is exact");
arc.sweep_degrees = 180;
const changedArcGeometry = traceSegmentGeometry(arc);
assert.notStrictEqual(changedArcGeometry, arcGeometry, "changing exact source geometry invalidates only that segment cache entry");
closeTo(changedArcGeometry.end.x, -10, "invalidated arc cache recomputes the new endpoint");
closeTo(changedArcGeometry.end.y, 0, "invalidated arc cache recomputes the new endpoint y");
arc.sweep_degrees = 90;

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

const benchmarkSegments = Array.from({ length: 4096 }, (_, index) => ({
  kind: "arc",
  center: { x: index * 3, y: index % 17 },
  radius_pixels: 2 + (index % 5),
  start_angle_degrees: index % 360,
  sweep_degrees: index % 2 ? 45 : -45,
}));
const benchmarkDraft = createTraceDraft(benchmarkSegments, { historyLimit: 8 });
const originalCos = Math.cos;
const originalSin = Math.sin;
let trigCalls = 0;
Math.cos = value => { trigCalls += 1; return originalCos(value); };
Math.sin = value => { trigCalls += 1; return originalSin(value); };
try {
  let revision = benchmarkDraft.current();
  for (const segment of revision) traceSegmentGeometry(segment);
  const initialTrigCalls = trigCalls;
  assert.equal(initialTrigCalls, 4096 * 4, "first render derives two endpoints for each arc exactly once");

  for (let interaction = 0; interaction < 64; interaction += 1) {
    const previous = revision;
    revision = benchmarkDraft.update(segments => segments.push({
      kind: "arc",
      center: { x: 20000 + interaction * 2, y: interaction },
      radius_pixels: 3,
      start_angle_degrees: interaction,
      sweep_degrees: 30,
    }));
    assert.strictEqual(revision[2048], previous[2048], "unchanged draft segments keep identity across tail edits");
    traceIsClosed(revision);
    for (const segment of revision) traceSegmentGeometry(segment);
  }
  assert.equal(
    trigCalls - initialTrigCalls,
    64 * 4,
    "64 redraw interactions derive trigonometry only for the changed tail segments",
  );
} finally {
  Math.cos = originalCos;
  Math.sin = originalSin;
}

const savedBenchmark = benchmarkDraft.snapshot();
assert.deepEqual(savedBenchmark, benchmarkDraft.current(), "derived rendering state never changes the authoritative save payload");
assert.notStrictEqual(savedBenchmark, benchmarkDraft.current(), "save payload remains an owned immutable snapshot boundary");
assert.equal(savedBenchmark.length, 4096 + 64, "large interaction benchmark preserves every exact source segment");

console.log("Scanner camera, cached traced-curve geometry, and 4096-segment interaction tests passed.");
