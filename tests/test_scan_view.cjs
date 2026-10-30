const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const element = () => {
  const listeners = {};
  const attributes = {};
  let source = "";
  const messages = [];
  return {
    listeners, attributes, dataset: {}, hidden: false, disabled: false, title: "", messages,
    contentWindow: { postMessage(message, origin) { messages.push({ message, origin }); } },
    get src() { return source; },
    set src(value) { source = value; attributes.src = value; },
    addEventListener(name, callback) { listeners[name] = callback; },
    setAttribute(name, value) { attributes[name] = value; },
    getAttribute(name) { return attributes[name] ?? null; },
    click() { if (!this.disabled) listeners.click?.(); },
  };
};
const livePollDelays = [];
const scheduleTestTimeout = (callback, delay, ...args) => {
  if (delay === 0 || delay >= 25) livePollDelays.push(delay);
  return setTimeout(callback, delay, ...args);
};
const mapAlignmentOutput = { hidden: true, textContent: "", dataset: {} };
const turntableAngleDisplay = { textContent: "0.0°" };
const turntableTiltDisplay = { textContent: "0.0°" };
const workspaceGrid = { dataset: { view: "scanning" } };
const testDocument = {
  getElementById: id => id === "scanner-map-alignment" ? mapAlignmentOutput
    : id === "turntable-angle" ? turntableAngleDisplay
      : id === "turntable-tilt" ? turntableTiltDisplay : null,
  querySelector: selector => selector === "#primary-grid" ? workspaceGrid : null,
};
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply&scan_origin_file=origin.ply"), setTimeout }, document: testDocument, URL, URLSearchParams, setTimeout: scheduleTestTimeout, clearTimeout, fetch: async () => ({ ok: true, async text() { return pointCloud; } }) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/scan-view.js"), "utf8"), sandbox);
let sampledLivePixels = 0;
const liveSampleSlots = new Set();
for (let y = 0; y < 400; y += 1) {
  for (let x = 0; x < 640; x += 1) {
    const slot = sandbox.window.CadyxScanView.livePreviewSampleIndex(x, y);
    if (slot >= 0) { sampledLivePixels += 1; liveSampleSlots.add(slot); }
  }
}
assert.equal(sampledLivePixels, 640 * 400, "live preview can address every pixel in the depth frame");
assert.equal(liveSampleSlots.size, 640 * 400, "each depth pixel maps to a unique stable history slot");
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(99.995), false);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(100), true);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(250), true);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(250.005), false);
assert.equal(sandbox.window.CadyxScanView.shouldRunTableMapRansac(3, 16, 8), true,
  "three frame-to-frame matches cannot register a map and must allow a throttled RANSAC recovery");
assert.equal(sandbox.window.CadyxScanView.shouldRunTableMapRansac(4, 16, 8), false,
  "four reliable tracks wait for the periodic global recheck");
assert.equal(sandbox.window.CadyxScanView.shouldRunTableMapRansac(2, 15, 8), false,
  "RANSAC remains throttled until eight frames have elapsed");
assert.equal(sandbox.window.CadyxScanView.shouldRunTableMapRansac(7, 16, 8, 34), true,
  "a partial marker subset periodically searches the full map for a better global fit");
assert.equal(sandbox.window.CadyxScanView.shouldRunTableMapRansac(34, 16, 8, 34), false,
  "a complete map match needs no periodic global search");
const turntableModel = sandbox.window.CadyxScanView.buildTurntableDisplayModel();
assert.equal(turntableModel.diameterMm, 200, "the display tabletop uses the user-provided 200 mm diameter");
assert.equal(turntableModel.thicknessMm, 10, "the display tabletop uses the user-provided 10 mm thickness");
assert.equal(turntableModel.supportRotationDegrees, -90,
  "the fixed lower support is turned 90 degrees clockwise independently of the tabletop");
const fixedSupportHasPoint = (x, y, z) => {
  for (let index = 0; index < turntableModel.fixed.positions.length; index += 3) {
    if (Math.abs(turntableModel.fixed.positions[index] - x) < 1e-4
        && Math.abs(turntableModel.fixed.positions[index + 1] - y) < 1e-4
        && Math.abs(turntableModel.fixed.positions[index + 2] - z) < 1e-4) return true;
  }
  return false;
};
assert.ok(fixedSupportHasPoint(0, 55, -25) && fixedSupportHasPoint(0, -55, -25),
  "the fixed pivot axle rotates with the lower support");
assert.ok(Array.from({ length: turntableModel.fixed.positions.length / 3 }, (_, index) => index)
  .some(index => Math.abs(turntableModel.fixed.positions[index * 3]) < 4.01
    && turntableModel.fixed.positions[index * 3 + 1] > 57.1
    && turntableModel.fixed.positions[index * 3 + 2] >= -29
    && turntableModel.fixed.positions[index * 3 + 2] <= -21),
"the front-facing turntable button is fixed on the zero-degree side of the support");
assert.ok(fixedSupportHasPoint(0, 99, -22.5),
  "the front bolt reaches the platter's front edge below the plate");
assert.equal(turntableModel.fitRadius, 120, "the outer degree labels have clearance from the viewport edge");
assert.equal(turntableModel.degreeTickCount, 72, "the turntable scale has 5 degree ticks through 360 degrees");
assert.equal(turntableModel.bakedDegreeLabelCount, 12,
  "twelve readable 30-degree labels are baked into the tabletop surface");
assert.deepEqual(Array.from(turntableModel.bakedDegreeLabels),
  ["0","30","60","90","120","150","180","210","240","270","300","330"],
  "baked degree values contain numbers without degree symbols");
assert.equal(turntableModel.bakedDegreeLabels[4], String(90 + 30),
  "the mark after 90 degrees is 120 degrees");
assert.equal(turntableModel.degreeLabelAlignment, "tangent",
  "baked labels follow a consistent tangent baseline around the circular scale");
assert.equal(turntableModel.degreeTextLabels.length, 0,
  "degree numerals do not depend on camera-gated canvas text");
assert.equal(turntableModel.degreeLabelPositions.length, 0,
  "baked numerals have no floating text anchors");
assert.ok(turntableModel.fixed.triangles.length > 0 && turntableModel.moving.triangles.length > 0,
  "the preview includes a shaded table base and a separately movable tabletop");
assert.equal(sandbox.window.CadyxScanView.hasClosedTriangleTopology(turntableModel.fixed.triangles), true,
  "the stationary turntable support is a closed, opaque triangle surface");
assert.equal(sandbox.window.CadyxScanView.hasClosedTriangleTopology(turntableModel.moving.triangles), true,
  "the moving tabletop, degree ticks and side marks are closed solids");
assert.equal(sandbox.window.CadyxScanView.hasClosedTriangleTopology(new Uint32Array([0, 1, 2])), false,
  "open preview surfaces are not classified as solid");
let lowerFlangeRadius = 0;
for (let index = 0; index < turntableModel.fixed.positions.length; index += 3) {
  if (Math.abs(turntableModel.fixed.positions[index + 2] + 80) < 1e-4) {
    lowerFlangeRadius = Math.max(lowerFlangeRadius,
      Math.hypot(turntableModel.fixed.positions[index], turntableModel.fixed.positions[index + 1]));
  }
}
assert.ok(Math.abs(lowerFlangeRadius - 96) < 1e-4, "the lower flange keeps its circular 192 mm footprint");
assert.equal(turntableModel.ribCount, 120, "the moving plate has fine circumferential grip ribs");
assert.equal(turntableModel.mountingRecessCount, 4, "the lower flange has four closed mounting recess details");
const ribRingRadii = new Set();
for (let index = 0; index < turntableModel.moving.positions.length; index += 3) {
  if (Math.abs(turntableModel.moving.positions[index + 2] + 0.9) < 1e-4) {
    const x = turntableModel.moving.positions[index], y = turntableModel.moving.positions[index + 1];
    ribRingRadii.add(Math.hypot(x, y).toFixed(3));
  }
}
assert.ok(ribRingRadii.size > 2, "the platter edge has visible radial variation instead of a smooth cylinder");
let maximumTopRadius = 0;
for (let index = 0; index < turntableModel.moving.positions.length; index += 3) {
  const x = turntableModel.moving.positions[index], y = turntableModel.moving.positions[index + 1];
  const z = turntableModel.moving.positions[index + 2];
  if (Math.abs(z) < 1e-5) maximumTopRadius = Math.max(maximumTopRadius, Math.hypot(x, y));
}
assert.ok(Math.abs(maximumTopRadius - 100) < 1e-4, "the displayed tabletop radius is exactly 100 mm");
assert.deepEqual(Array.from(sandbox.window.CadyxScanView.buildPreviewCloudPointIndices(8, 1, [1, 6], true)),
  [0, 2, 3, 4, 5, 7], "unregistered bright candidates are omitted from the mapped marker preview");
assert.deepEqual(Array.from(sandbox.window.CadyxScanView.buildPreviewCloudPointIndices(8, 1, [1, 6], false)),
  [0, 1, 2, 3, 4, 5, 6, 7], "unmapped diagnostic views retain their detected point samples");
assert.deepEqual(Array.from(sandbox.window.CadyxScanView.buildMatchedMapMarkerPointIndices(
  { markerPoints: new Uint32Array([10, 11, 12]) }, [2, 0, 99])), [12, 10],
  "only confirmed correspondences highlight exact map marker locations");
const rotatedPoint = sandbox.window.CadyxScanView.transformTableDisplayPoint([0, 10, 0], 90, 0);
assert.ok(Math.abs(rotatedPoint[0] + 10) < 1e-5 && Math.abs(rotatedPoint[1]) < 1e-5,
  "turntable encoder rotation changes only the display-frame point position");
const tiltedPoint = sandbox.window.CadyxScanView.transformTableDisplayPoint([0, 0, 0], 0, 30);
assert.ok(Math.abs(tiltedPoint[1] + 12.5) < 1e-5 && tiltedPoint[2] < 0,
  "the display model follows a 30 degree table tilt around its horizontal pivot");

const pointCloud = "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nelement face 0\nend_header\n0 0 -120\n1 0 -119.8\n0 1 -120.1\n";
const originPoints = [];
for (let y = -35; y < 35; y += 1) for (let x = -35; x < 35; x += 1) originPoints.push(`${x} ${y} ${-120 + 0.2 * x - 0.1 * y}`);
const originCloud = `ply\nformat ascii 1.0\nelement vertex ${originPoints.length}\nproperty float x\nproperty float y\nproperty float z\nelement face 0\nend_header\n${originPoints.join("\n")}\n`;
const viewerLoads = [];
let viewerCamera = { orientation: [1, 0, 0, 0, 1, 0, 0, 0, 1], zoom: 1, panX: 0, panY: 0, projection: "orthographic" };
const viewerCameraUpdates = [];
const viewer = {
  load(geometry, options) { viewerLoads.push({ geometry, options }); },
  getCamera() { return viewerCamera; },
  setCamera(camera) { viewerCamera = camera; viewerCameraUpdates.push(camera); },
};
const baseGeometry = { positions: new Float64Array([0, 0, 0]), triangles: new Uint32Array(0), bounds: { min: [0, 0, 0], max: [0, 0, 0] } };
const getModelGeometry = () => baseGeometry;
const fetchCloud = async url => ({ ok: true, async text() { assert.match(url, /point-cloud\?/); return pointCloud; } });
const liveRawFrames = [new Uint8Array(640 * 400 * 2), new Uint8Array(640 * 400 * 2)];
let liveRawFrameSource = liveRawFrames;
const centers = [[240, 180], [400, 180], [240, 220], [400, 220], [320, 200]];
const zAxis = [0.12, 0.08, 1].map(value => value / Math.hypot(0.12, 0.08, 1));
let xAxis = [1 - zAxis[0] * zAxis[0], -zAxis[0] * zAxis[1], -zAxis[0] * zAxis[2]];
xAxis = xAxis.map(value => value / Math.hypot(...xAxis));
const yAxis = [zAxis[1] * xAxis[2] - zAxis[2] * xAxis[1],
  zAxis[2] * xAxis[0] - zAxis[0] * xAxis[2], zAxis[0] * xAxis[1] - zAxis[1] * xAxis[0]];
const mapBuffer = new ArrayBuffer(16 + centers.length * 64);
const mapView = new DataView(mapBuffer);
mapView.setUint32(0, 1, true);
mapView.setUint32(12, centers.length, true);
for (let index = 0; index < centers.length; index += 1) {
  const [centerX, centerY] = centers[index];
  const px = centerX - 320, py = 200 - centerY;
  const depth = 130 + 0.12 * px + 0.08 * py;
  const cameraPoint = [px, py, -depth];
  const x = xAxis.reduce((sum, value, axis) => sum + value * cameraPoint[axis], 0);
  const y = yAxis.reduce((sum, value, axis) => sum + value * cameraPoint[axis], 0);
  const offset = 16 + index * 64;
  mapView.setInt32(offset, 0, true);
  mapView.setInt32(offset + 4, -1, true);
  mapView.setFloat32(offset + 8, 1, true);
  mapView.setFloat32(offset + 12, 0, true);
  for (let axis = 0; axis < 3; axis += 1) mapView.setFloat64(offset + 16 + axis * 8, x * xAxis[axis] + y * yAxis[axis], true);
  for (let axis = 0; axis < 3; axis += 1) mapView.setFloat64(offset + 40 + axis * 8, zAxis[axis], true);
}
assert.throws(() => sandbox.window.CadyxScanView.registerTableMarkers(
  [[0, 0], [10, 0], [20, 0], [30, 0]], { points: [[0, 0], [10, 0], [20, 0], [30, 0]] }, 2,
), /почти на одной линии/, "collinear map detections cannot define a reliable table frame");
const uniqueMarkerPattern = [
  [0, 0], [20, 0], [0, 20], [20, 20],
  [100, 2], [120, -1], [102, 22], [118, 18],
];
const recoveredMarkerFit = sandbox.window.CadyxScanView.registerTableMarkers(
  uniqueMarkerPattern, { points: uniqueMarkerPattern }, 5,
  [0, 1, 2, 3].map(index => ({ sourceIndex: index, targetIndex: index + 4 })), true,
);
assert.equal(recoveredMarkerFit.matches.length, uniqueMarkerPattern.length,
  "periodic global registration replaces a plausible partial tracked fit with the complete unique-pattern fit");
for (const frame of liveRawFrames) {
  for (const [centerX, centerY] of centers) {
    for (let y = centerY - 16; y <= centerY + 16; y += 1) for (let x = centerX - 16; x <= centerX + 16; x += 1) {
      if ((x - centerX) ** 2 + (y - centerY) ** 2 > 16 ** 2) continue;
      const pixel = y * 640 + x;
      const depth = Math.round(130 + 0.12 * (x - 320) - 0.08 * (y - 200));
      frame[pixel * 2] = depth & 0xff;
      frame[pixel * 2 + 1] = depth >> 8;
    }
  }
  for (let y = 280; y < 340; y += 1) for (let x = 280; x < 360; x += 1) {
    const pixel = y * 640 + x;
    frame[pixel * 2] = 110;
  }
}
let liveFrameSequence = 0;
const waitUntil = async predicate => {
  const deadline = Date.now() + 2000;
  while (!predicate() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
};
const fetchPreview = async url => {
  if (url.includes("point-cloud?")) return { ok: true, async text() { return url.includes("origin.ply") ? originCloud : pointCloud; } };
  if (url.includes("/turntable/map")) return { ok: true, async arrayBuffer() { return mapBuffer; } };
  if (url.includes("/calibration")) return { ok: true, async json() { return { width: 640, height: 400, matrix: [1, 0, 0, -320, 0, 1, 0, -200, 0, 0, 1, 0, 0, 0, 0, 0], depth_scale_mm_per_raw_unit: 1 }; } };
  if (url.includes("/live-frame")) {
    const index = liveFrameSequence++;
    if (index >= liveRawFrameSource.length) return { ok: false, status: 404 };
    return { ok: true, headers: { get(name) {
      return name === "X-CADYX-Frame-Mtime"
        ? String(1_800_000_000_000_000_000 + index * 250_000_000) : String(1000 + index);
    } }, async arrayBuffer() { return liveRawFrameSource[index].buffer; } };
  }
  throw new Error(`Unexpected preview request: ${url}`);
};

async function verifyScanView() {
const cloudButton = element();
const meshButton = element();
const viewport = element();
const view = sandbox.window.CadyxScanView.initialize({ cloudButton, meshButton, viewport, viewer, getModelGeometry, meshReady: true }, undefined, fetchPreview);
cloudButton.click();
await new Promise(resolve => setImmediate(resolve));
assert.equal(cloudButton.attributes["aria-pressed"], "true");
assert.equal(viewport.dataset.scanPreview, "false");
assert.equal(viewport.dataset.scanPreviewMode, "cloud");
assert.equal(viewerLoads.at(-1).geometry.positions.length, 9);
assert.equal(viewerLoads.at(-1).geometry.triangles.length, 0);
assert.ok(Math.abs(viewerLoads.at(-1).geometry.bounds.min[2]) < 0.01 && Math.abs(viewerLoads.at(-1).geometry.bounds.max[2]) < 0.01,
  "the saved table plane is mapped to XY using the first-frame source cloud");
meshButton.click();
await new Promise(resolve => setImmediate(resolve));
assert.equal(viewport.dataset.scanPreview, "false");
assert.equal(viewport.dataset.scanPreviewMode, "mesh");
assert.equal(cloudButton.attributes["aria-pressed"], "false");
assert.equal(meshButton.attributes["aria-pressed"], "true");
assert.equal(viewerLoads.at(-1).geometry, baseGeometry);
cloudButton.click();
await new Promise(resolve => setImmediate(resolve));
assert.equal(viewerLoads.at(-1).geometry.positions.length, 9);
assert.equal(cloudButton.title, "", "preview state does not add a tooltip");
view.setOpen(false);
assert.equal(viewport.dataset.scanPreview, "false");
assert.ok(viewerLoads.at(-1).geometry.triangles.length > 0,
  "closing the cloud leaves the static turntable in the same canvas");
assert.equal(viewport.dataset.scanPreviewMode, "turntable");
view.setOpen(true);
await new Promise(resolve => setImmediate(resolve));
assert.equal(viewerLoads.at(-1).options.reset, false, "reopening the same cloud preserves the shared viewer orbit");
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", origin_file: "origin.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
await new Promise(resolve => setImmediate(resolve));
assert.match(viewerLoads.at(-1).geometry.positions.length.toString(), /^9$/);
assert.ok(Math.abs(viewerLoads.at(-1).geometry.bounds.min[2]) < 0.01 && Math.abs(viewerLoads.at(-1).geometry.bounds.max[2]) < 0.01,
  "registered fusion uses the first-frame table coordinate system");
meshButton.click();
await new Promise(resolve => setImmediate(resolve));
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", origin_file: "origin.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(viewerLoads.at(-1).geometry, baseGeometry, "the grid view uses the CAD mesh in the same viewer");
const loadCount = viewerLoads.length;
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", origin_file: "origin.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(viewerLoads.length, loadCount, "repeated status polls do not reload the mesh or reset the camera");
liveFrameSequence = 0;
const livePreviewLoadStart = viewerLoads.length;
view.showLivePreview({ open: true });
let mappedLiveGeometry = null;
await waitUntil(() => {
  if (viewerLoads.length < livePreviewLoadStart + 3) return false;
  const geometry = viewerLoads.at(-1)?.geometry;
  if (!geometry?.pointLayers?.some(layer => layer.style?.color === "#00d8ff")) return false;
  mappedLiveGeometry = geometry;
  return true;
});
assert.equal(viewport.dataset.scanPreview, "true", "live scanner frames are rendered in the main preview canvas");
assert.equal(viewport.dataset.scanPreviewMode, "live");
assert.equal(mappedLiveGeometry.pointPreviewLimit, 8_000);
const emptyTableGeometry = mappedLiveGeometry;
const mappedMarkerLayer = emptyTableGeometry.pointLayers.find(layer => layer.style.color === "#d4f6ff");
const matchedMarkerLayer = emptyTableGeometry.pointLayers.find(layer => layer.style.color === "#00d8ff");
assert.equal(emptyTableGeometry.points.length, 0, "raw scan samples do not masquerade as map markers");
assert.equal(mappedMarkerLayer.points.length, centers.length, "all saved map markers remain visible");
assert.equal(matchedMarkerLayer.points.length, centers.length, "only map-confirmed current markers are highlighted");
assert.equal(matchedMarkerLayer.style.size, 12);
assert.equal(mappedMarkerLayer.style.shape, "circle");
assert.equal(matchedMarkerLayer.style.shape, "circle");
const markerPositions = emptyTableGeometry.positions;
for (const pointIndex of [...mappedMarkerLayer.points, ...matchedMarkerLayer.points]) {
  const index = pointIndex * 3;
  assert.ok(Math.abs(markerPositions[index + 2]) < 2, "map markers lie on the tabletop plane");
  assert.ok(Math.hypot(markerPositions[index], markerPositions[index + 1]) <= 100,
    "every shown map marker lies inside the 200 mm tabletop");
}
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0],
  "the mapped table-marker centroid is the preview origin");
const firstMappedMarker = [...markerPositions.slice(mappedMarkerLayer.points[0] * 3,
  mappedMarkerLayer.points[0] * 3 + 3)];
const firstLiveFitRadius = viewerLoads.at(-1).geometry.fitRadius;
await waitUntil(() => livePollDelays.includes(100));
assert.ok(liveFrameSequence >= 2, "the live point cloud accepted a newer camera frame");
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0], "live frames keep the first-frame camera pivot");
assert.equal(viewerLoads.at(-1).geometry.fitRadius, firstLiveFitRadius, "live frames keep a fixed view scale");
assert.equal(viewerLoads.at(-1).options.reset, false, "new live frames never reset the camera");
const nextMappedMarkerLayer = viewerLoads.at(-1).geometry.pointLayers.find(layer => layer.style.color === "#d4f6ff");
const nextMappedMarker = [...viewerLoads.at(-1).geometry.positions.slice(nextMappedMarkerLayer.points[0] * 3,
  nextMappedMarkerLayer.points[0] * 3 + 3)];
assert.ok(Math.hypot(...nextMappedMarker.map((value, axis) => value - firstMappedMarker[axis])) < 1e-4,
  "identical scanner frames keep table markers at their exact map positions");
assert.ok(livePollDelays.includes(100),
  `idle frame polling adapts to the measured 250 ms camera cadence (scheduled: ${livePollDelays.join(",")})`);
view.showCapture({ preview_folder: "scan folder", preview_file: "new-saved-capture.ply", preview_kind: "single" });
assert.equal(viewport.dataset.scanPreview, "true", "360 status polling does not replace the live canvas preview");
view.closeLivePreview();
assert.equal(viewport.dataset.scanPreview, "false", "stopping the scanner preview leaves only the static table");
assert.equal(viewport.dataset.scanPreviewMode, "turntable",
  "the table remains visible after the scanner preview is stopped");
assert.ok(viewerLoads.at(-1).geometry.triangles.length > 0,
  "the idle Scanning view renders the display-only table");
assert.deepEqual(Array.from(viewerLoads.at(-1).geometry.surfaceTriangleRanges, range => [range.start, range.end]),
  [[0, viewerLoads.at(-1).geometry.triangles.length / 3]],
  "the scanner table uses opaque shaded faces without internal feature-edge overlays");
assert.equal(viewerLoads.at(-1).geometry.pointLayers.length, 0,
  "the idle table view does not fabricate live markers");
assert.equal(viewerLoads.at(-1).options.reset, false,
  "showing the idle turntable preserves the user's camera");
assert.equal(view.hideTurntable(), true);
assert.equal(viewport.dataset.scanPreviewMode, "");
assert.equal(viewerLoads.at(-1).geometry, baseGeometry,
  "leaving Scanning restores the project model in the same viewport");

const makeMapAlignedFrame = () => {
  const frame = new Uint8Array(640 * 400 * 2);
  for (const [centerX, centerY] of centers) {
    for (let y = centerY - 7; y <= centerY + 7; y += 1) for (let x = centerX - 7; x <= centerX + 7; x += 1) {
      if ((x - centerX) ** 2 + (y - centerY) ** 2 > 49) continue;
      const pixel = y * 640 + x;
      const depth = Math.round(135 + 0.12 * (x - 320) - 0.08 * (y - 200));
      frame[pixel * 2] = depth & 0xff;
      frame[pixel * 2 + 1] = depth >> 8;
    }
  }
  for (let y = 275; y < 287; y += 1) for (let x = 300; x < 312; x += 1) frame[(y * 640 + x) * 2] = 110;
  return frame;
};
const unshiftedMapFrame = makeMapAlignedFrame();
const translatedMapFrame = new Uint8Array(640 * 400 * 2);
for (let y = 0; y < 400; y += 1) for (let x = 0; x < 640; x += 1) {
  const depthOffset = (y * 640 + x) * 2;
  if (unshiftedMapFrame[depthOffset] === 0 && unshiftedMapFrame[depthOffset + 1] === 0) continue;
  const translatedX = x + 20, translatedY = y + 12;
  const translatedOffset = (translatedY * 640 + translatedX) * 2;
  translatedMapFrame[translatedOffset] = unshiftedMapFrame[depthOffset];
  translatedMapFrame[translatedOffset + 1] = unshiftedMapFrame[depthOffset + 1];
}
liveRawFrameSource = [unshiftedMapFrame, translatedMapFrame, new Uint8Array(640 * 400 * 2)];
liveFrameSequence = 0;
sandbox.window.cadyxUiReloadCamera = {
  orientation: [0, 0, -1, 0, 1, 0, 1, 0, 0], zoom: 1.7, panX: 13, panY: -9, projection: "orthographic",
};
const savedReloadCamera = sandbox.window.cadyxUiReloadCamera;
const mapAlignedView = sandbox.window.CadyxScanView.initialize({
  cloudButton: element(), meshButton: element(), viewport: element(), viewer, getModelGeometry, meshReady: true,
}, undefined, fetchPreview);
const mapAlignedStart = viewerLoads.length;
mapAlignedView.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.length >= mapAlignedStart + 3
  && liveFrameSequence >= 2);
assert.ok(viewerCameraUpdates.includes(savedReloadCamera),
  "the saved camera is restored after the first table-map scene load");
assert.equal(viewerLoads[mapAlignedStart + 1].options.reset, false,
  "first marker alignment does not replace the restored user camera");
const mapAlignedGeometries = [viewerLoads[mapAlignedStart + 1].geometry, viewerLoads[mapAlignedStart + 2].geometry];
const mappedMarkerCoordinates = mapAlignedGeometries.map(geometry => {
  const layer = geometry.pointLayers.find(item => item.style.color === "#d4f6ff");
  const matchedLayer = geometry.pointLayers.find(item => item.style.color === "#00d8ff");
  assert.equal(layer.points.length, centers.length);
  assert.equal(matchedLayer.points.length, centers.length);
  assert.equal(geometry.pointLayers.some(item => item.style.color === "#ff9d54"), false,
    "an empty tabletop contains no live surface cloud");
  return [...matchedLayer.points].map(index => [...geometry.positions.slice(index * 3, index * 3 + 3)]);
});
assert.deepEqual(mappedMarkerCoordinates[0], mappedMarkerCoordinates[1],
  "mapped marker highlights stay at exact table-map coordinates as image-space detections move");
assert.match(mapAlignmentOutput.textContent, /Карта \d+\/5/,
  "the live preview reports its current table-map correspondence quality");
await waitUntil(() => liveFrameSequence >= 3);
await new Promise(resolve => setTimeout(resolve, 30));
assert.equal(viewerLoads.length, mapAlignedStart + 3,
  "a frame without enough table markers does not replace the last map-aligned cloud with unaligned points");
assert.match(mapAlignmentOutput.textContent, /последний кадр/,
  "the map status explains when the preview is retaining its last aligned frame");
const lastAlignedGeometry = viewerLoads.at(-1).geometry;
const lastMapLayer = lastAlignedGeometry.pointLayers.find(layer => layer.style.color === "#d4f6ff");
const lastMatchedLayer = lastAlignedGeometry.pointLayers.find(layer => layer.style.color === "#00d8ff");
const mapBeforeDisplayRotation = [...lastAlignedGeometry.positions.slice(lastMapLayer.points[0] * 3,
  lastMapLayer.points[0] * 3 + 3)];
const matchedBeforeDisplayRotation = [...lastAlignedGeometry.positions.slice(lastMatchedLayer.points[0] * 3,
  lastMatchedLayer.points[0] * 3 + 3)];
const beforeDisplayRotationLoads = viewerLoads.length;
turntableAngleDisplay.textContent = "15.0°";
await waitUntil(() => viewerLoads.length > beforeDisplayRotationLoads);
const rotatedGeometry = viewerLoads.at(-1).geometry;
const rotatedMapLayer = rotatedGeometry.pointLayers.find(layer => layer.style.color === "#d4f6ff");
const rotatedMatchedLayer = rotatedGeometry.pointLayers.find(layer => layer.style.color === "#00d8ff");
const rotatedMap = [...rotatedGeometry.positions.slice(rotatedMapLayer.points[0] * 3,
  rotatedMapLayer.points[0] * 3 + 3)];
const rotatedMatched = [...rotatedGeometry.positions.slice(rotatedMatchedLayer.points[0] * 3,
  rotatedMatchedLayer.points[0] * 3 + 3)];
assert.deepEqual([...rotatedGeometry.positions.slice(0, 3)],
  [...lastAlignedGeometry.positions.slice(0, 3)], "the circular lower support stays fixed while the tabletop moves");
for (const [actual, before] of [[rotatedMap, mapBeforeDisplayRotation],
  [rotatedMatched, matchedBeforeDisplayRotation]]) {
  const expected = sandbox.window.CadyxScanView.transformTableDisplayPoint(before, 15, 0);
  assert.ok(Math.hypot(...actual.map((value, axis) => value - expected[axis])) < 1e-4,
    "the confirmed map markers rotate together with the table display");
}
mapAlignedView.closeLivePreview();

const makeUnalignedFrame = (markerCenters, extraDepth = 0) => {
  const frame = new Uint8Array(640 * 400 * 2);
  for (const [centerX, centerY] of markerCenters) {
    for (let y = centerY - 16; y <= centerY + 16; y += 1) for (let x = centerX - 16; x <= centerX + 16; x += 1) {
      if ((x - centerX) ** 2 + (y - centerY) ** 2 > 16 ** 2) continue;
      const pixel = y * 640 + x, depth = 135 + extraDepth;
      frame[pixel * 2] = depth & 0xff;
      frame[pixel * 2 + 1] = depth >> 8;
    }
  }
  return frame;
};
liveRawFrameSource = [
  makeUnalignedFrame(centers.slice(0, 3)),
  makeUnalignedFrame(centers.slice(0, 3).map(([x, y]) => [x + 2, y + 1]), 12),
  new Uint8Array(640 * 400 * 2),
  new Uint8Array(640 * 400 * 2),
];
const displayedLiveCloudCount = geometry => Number.isInteger(geometry.pointCloudVertexCount)
  ? geometry.pointCloudVertexCount : geometry.pointLayers?.find(layer => layer.style.color === "#ff9d54")?.points.length ?? 0;
liveFrameSequence = 0;
const unalignedButton = element();
const unalignedView = sandbox.window.CadyxScanView.initialize({
  cloudButton: unalignedButton,
  meshButton: element(),
  viewport: element(),
  viewer,
  getModelGeometry,
  meshReady: true,
}, undefined, fetchPreview);
const unalignedStart = viewerLoads.length;
unalignedView.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.length >= unalignedStart + 3);
const firstUnaligned = viewerLoads[unalignedStart + 1].geometry;
const secondUnaligned = viewerLoads[unalignedStart + 2].geometry;
assert.equal(displayedLiveCloudCount(firstUnaligned), 0,
  "unmatched marker candidates are hidden until they can be placed on the table map");
assert.equal(firstUnaligned.pointLayers.find(layer => layer.style.color === "#d4f6ff").points.length, centers.length,
  "the saved marker map remains visible while live detections wait for enough correspondences");
assert.deepEqual([...secondUnaligned.orbitCenter], [...firstUnaligned.orbitCenter],
  "unregistered live frames retain the first valid frame camera center");
assert.equal(secondUnaligned.fitRadius, firstUnaligned.fitRadius,
  "unregistered live frames retain the first valid frame camera scale");
assert.deepEqual([...secondUnaligned.bounds.min], [...firstUnaligned.bounds.min],
  "unmatched detections cannot move the mapped table or marker display");
await waitUntil(() => liveFrameSequence >= 4);
await new Promise(resolve => setTimeout(resolve, 20));
assert.equal(viewerLoads.length, unalignedStart + 3,
  "empty sensor frames do not create synthetic or blended point clouds");
unalignedView.closeLivePreview();

liveRawFrameSource = [
  makeUnalignedFrame(centers.slice(0, 3)),
  makeUnalignedFrame(centers.slice(0, 3).map(([x, y]) => [x + 150, y]), 12),
  new Uint8Array(640 * 400 * 2),
];
liveFrameSequence = 0;
const movingView = sandbox.window.CadyxScanView.initialize({
  cloudButton: element(), meshButton: element(), viewport: element(), viewer, getModelGeometry, meshReady: true,
}, undefined, fetchPreview);
const movingStart = viewerLoads.length;
movingView.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.length >= movingStart + 3);
const movingSecondGeometry = viewerLoads[movingStart + 2].geometry;
assert.equal(displayedLiveCloudCount(movingSecondGeometry), 0,
  "live detections outside reliable map correspondences are not shown as table markers");
assert.equal(movingSecondGeometry.pointLayers.some(layer => layer.style.color === "#00d8ff"), false,
  "a moving unmatched set never paints false marker highlights");
movingView.closeLivePreview();

const denseFrame = new Uint8Array(640 * 400 * 2);
for (let y = 50; y < 350; y += 1) for (let x = 100; x < 300; x += 1) {
  const pixel = y * 640 + x;
  denseFrame[pixel * 2] = 150;
}
liveRawFrameSource = [denseFrame, denseFrame];
liveFrameSequence = 0;
const denseView = sandbox.window.CadyxScanView.initialize({
  cloudButton: element(), meshButton: element(), viewport: element(), viewer, getModelGeometry, meshReady: true,
}, undefined, fetchPreview);
const denseStart = viewerLoads.length;
denseView.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.length >= denseStart + 3
  && displayedLiveCloudCount(viewerLoads.at(-1)?.geometry || {}) > 0);
const firstDenseGeometry = viewerLoads[denseStart + 1].geometry;
const secondDenseGeometry = viewerLoads[denseStart + 2].geometry;
const denseDisplayedCount = displayedLiveCloudCount(secondDenseGeometry);
assert.ok(denseDisplayedCount <= 8_000, "dense scanner frames respect the preview point ceiling");
assert.ok(denseDisplayedCount >= 7_000,
  `stable density sampling uses nearly the full point budget: ${denseDisplayedCount}`);
assert.deepEqual(
  [...secondDenseGeometry.positions.slice(0, denseDisplayedCount * 3)],
  [...firstDenseGeometry.positions.slice(0, denseDisplayedCount * 3)],
  "dense-frame sampling keeps the same pixels selected across identical frames",
);
denseView.closeLivePreview();
liveRawFrameSource = liveRawFrames;

const blockedCloudButton = element();
const blockedMeshButton = element();
const blocked = sandbox.window.CadyxScanView.initialize({
  cloudButton: blockedCloudButton, meshButton: blockedMeshButton, viewport: element(), viewer, getModelGeometry, meshReady: false,
}, undefined, fetchPreview);
assert.equal(blockedMeshButton.disabled, true, "mesh stays disabled until point cloud processing is confirmed");
blockedCloudButton.click();
await new Promise(resolve => setImmediate(resolve));
assert.equal(blockedCloudButton.attributes["aria-pressed"], "true", "the cloud control renders in the existing canvas while mesh is unavailable");
assert.equal(viewerLoads.at(-1).geometry.positions.length, 9);
blockedCloudButton.click();
await new Promise(resolve => setImmediate(resolve));
assert.equal(blockedCloudButton.attributes["aria-pressed"], "false", "closing the cloud preview does not generate a mesh");
blockedMeshButton.click();
blocked.setOpen(true);
blocked.setMode("mesh");
assert.equal(blockedMeshButton.attributes["aria-pressed"], "false");

const polledCloudButton = element();
const polled = sandbox.window.CadyxScanView.initialize({
  cloudButton: polledCloudButton, meshButton: element(), viewport: element(), viewer, getModelGeometry, meshReady: false,
}, undefined, fetchPreview);
const capture = { preview_folder: "scan folder", preview_subfolder: "run-1", preview_file: "frame-1.ply",
  origin_file: "origin.ply", preview_kind: "single" };
polled.showCapture(capture);
assert.equal(polledCloudButton.attributes["aria-pressed"], "false", "background scan polling does not open a closed preview");
polled.showCapture({ ...capture, state: "running" });
await new Promise(resolve => setImmediate(resolve));
assert.equal(polledCloudButton.attributes["aria-pressed"], "true",
  "the first saved frame opens in the main canvas while capture is running");
polled.setOpen(false);
polled.showCapture({ ...capture, state: "running", preview_file: "frame-2.ply" });
assert.equal(polledCloudButton.attributes["aria-pressed"], "false", "new frames update without reopening a preview the user closed");

const serverPointCloudButton = element();
const serverModelGeometry = {
  positions: new Float64Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), triangles: new Uint32Array(0),
  points: new Uint32Array([0, 1, 2]), bounds: { min: [0, 0, 0], max: [1, 1, 0] },
};
const serverPreview = sandbox.window.CadyxScanView.initialize({
  cloudButton: serverPointCloudButton, meshButton: element(), viewport: element(), viewer,
  getModelGeometry: () => serverModelGeometry,
  hasPersistentPointCloud: (file, scan) => file === "merged.ply"
    || Boolean(scan?.preview_subfolder && "run-1_table_xy_candidate.ply".startsWith(`${scan.preview_subfolder}_`)),
  meshReady: false,
}, undefined, fetchPreview);
serverPreview.showCapture({ ...capture, state: "running" }, { open: true });
await new Promise(resolve => setImmediate(resolve));
assert.equal(serverPointCloudButton.attributes["aria-pressed"], "true");
serverPreview.showCapture({ ...capture, state: "completed", preview_file: "frame-2_table_aligned.ply" });
assert.equal(serverPointCloudButton.disabled, true, "a persisted fusion stays in the CAD model viewer");
assert.equal(serverPointCloudButton.attributes["aria-pressed"], "false");
assert.equal(viewerLoads.at(-1).geometry, serverModelGeometry, "completion restores the server-rendered point-cloud body");
serverPointCloudButton.click();
assert.equal(viewerLoads.at(-1).geometry, serverModelGeometry, "the scan overlay cannot replace the persisted CAD cloud");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const styleCss = fs.readFileSync(path.join(__dirname, "../web/style.css"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const projectUiJs = fs.readFileSync(path.join(__dirname, "../web/ply-preview.js"), "utf8");
assert.match(html, /id="viewport-live-scan"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="viewport-scan-mesh"[^>]*>Сетка<\/button>/);
assert.match(html, /id="viewport-scan-360"[^>]*>Сканировать 360°<\/button>/);
assert.match(html, /id="viewport-live-camera"[^>]*>Предпросмотр<\/button>/);
assert.match(html, /id="scanner-preview-fps"[^>]*>FPS —<\/output>/,
  "the scanner preview shows only the sensor FPS indicator");
assert.match(html, /id="scanner-map-alignment"[^>]*aria-label="Сопоставление точек с картой стола"/,
  "the scanner viewport exposes the active table-map alignment status");
assert.match(projectUiJs, /function projectPath\(projectId\)/);
assert.match(projectUiJs, /function projectIdForPathSlug\(requestedSlug\)/);
assert.match(projectUiJs, /let suffix = 2/);
assert.match(projectUiJs, /const cameraPresets = window\.CadyxViewportHelpers\.CAMERA_PRESETS/,
  "the visible orthographic views and toolbar share one canonical orientation map");
assert.match(projectUiJs, /#ortho-front[\s\S]*camera: cameraPresets\.front/);
assert.match(projectUiJs, /#ortho-right[\s\S]*camera: cameraPresets\.right/);
assert.match(projectUiJs, /if \(activeView === "scanning"\) \{\s*initializeScanningFrontView\(\);/,
  "the first Scanning view uses the front camera while later user camera choices remain restorable");
assert.match(projectUiJs,
  /const addressView = new URLSearchParams\(window\.location\.search\)\.get\("view"\);\s*const workspaceView = restorableWorkspaceViews\.has\(addressView\) \? addressView[\s\S]*: primaryGrid\.dataset\.view;/,
  "an explicit URL view wins over a temporary Overview while CADYX restores the selected project");
assert.match(projectUiJs, /const activeView = views\.has\(view\)\s*&& \(view !== "files" \|\| canOpenFilesView\(\)\) \? view : "overview";/,
  "a read-only workspace view remains selected while the project is busy or unavailable");
assert.match(projectUiJs, /const workspaceNavigation = Boolean\(button\.dataset\.workspaceView\);\s*const projectNavigation = button\.dataset\.projectNavigation === "true";\s*button\.disabled = \(!projectsNavigation && !projectNavigation && !workspaceNavigation && !available\) \|\| filesUnavailable;/,
  "workspace navigation remains usable while mutation controls stay locked");
assert.doesNotMatch(projectUiJs, /searchParams\.set\("project_id"/);
assert.doesNotMatch(projectUiJs, /window\.location\.assign\(`\/\?project_id=/);
assert.match(projectUiJs, /await navigateToWorkspaceProject\(result\.project\.id/);
assert.ok(html.indexOf('id="viewport-live-scan"') < html.indexOf('id="viewport-scan-360"'));
assert.doesNotMatch(html, /live-scan-tabs|live-scan-cloud-tab|live-scan-mesh-tab/);
assert.doesNotMatch(html, /live-scan-layer|live-scan-frame/);
const navigationStart = html.indexOf('<nav id="workspace-navigation"');
const navigationMarkup = html.slice(navigationStart, html.indexOf("</nav>", navigationStart));
const productionTab = navigationMarkup.indexOf('data-workspace-view="manufacturing"');
const scanningTab = navigationMarkup.indexOf('data-workspace-view="scanning"');
const activityTab = navigationMarkup.indexOf('data-workspace-view="activity"');
assert.ok(productionTab >= 0 && productionTab < scanningTab && scanningTab < activityTab,
  "the Scanning workspace tab follows Manufacturing and precedes Activity");
assert.doesNotMatch(html, /id="scanning-frame"/, "live scanner preview uses the main viewport without a second iframe");
assert.match(appJs, /viewer:modelViewer/);
assert.match(appJs, /window\.cadyxSyncCaptureButton=syncCaptureButton/);
assert.match(appJs, /if\(scan360\.dataset\.captureLocked==='true'\)return/);
assert.match(styleCss, /#viewport-scan-360\[data-capture-locked="true"\]\s*\{\s*opacity:\s*\.43;\s*pointer-events:\s*none;/);
assert.match(styleCss, /\.scanner-preview-fps\s*\{[^}]*right:\s*12px;[^}]*bottom:\s*12px;/,
  "the FPS badge sits at the bottom-right of the viewport");
assert.match(styleCss, /\.scanner-map-alignment\s*\{[^}]*left:\s*82px;[^}]*bottom:\s*12px;/,
  "the marker-map alignment status is visible at the bottom-left of the viewport");
assert.match(styleCss, /\.primary-grid:not\(\[data-view="scanning"\]\) #viewport-live-camera,[\s\S]*?#scanner-map-alignment \{ display: none !important; \}/,
  "scanner controls and overlays are hidden outside the Scanning tab");
const scanViewSource = fs.readFileSync(path.join(__dirname, "../web/scan-view.js"), "utf8");
assert.match(appJs, /if\(P&&document\.querySelector\('#primary-grid'\)\?\.dataset\.view!=='scanning'\)\{/,
  "selected-body name badges do not cover the scanner turntable preview");
assert.match(appJs, /style\.textContent \+= '\.primary-grid\[data-view="scanning"\] \.cad3-overlay\{display:none!important\}'/,
  "the viewport selection canvas is hidden over the scanner turntable");
assert.doesNotMatch(scanViewSource, /interactiveRenderScale/,
  "the live turntable keeps full-resolution rendering while rotating");
assert.match(appJs, /grid\?\.dataset\.view==='scanning'\)scanView\?\.showTurntable\(\)/,
  "entering Scanning shows the static table even when live preview is stopped");
assert.match(projectUiJs, /if \(activeView === "scanning"\) \{\s*initializeScanningFrontView\(\);/,
  "navigating to Scanning initializes its front camera without calling a missing global method");
assert.doesNotMatch(projectUiJs, /window\.CadyxScanView\?\.showTurntable\(\)/,
  "the static table is shown by the scan-view instance owned by app.js");
assert.match(appJs, /scanView\?\.hideTurntable\(\)/,
  "leaving Scanning restores the project viewport in the shared canvas");
assert.match(scanViewSource, /const captureFps = preview\?\.metrics\?\.capture_fps/,
  "the badge uses the native measured sensor rate");
assert.doesNotMatch(fs.readFileSync(path.join(__dirname, "../web/viewport.js"), "utf8"),
  /livePointCanvases|setPointLayerOpacities|setPointInterpolationProgress/,
  "the latest-frame preview does not animate between clouds");
assert.match(scanViewSource, /live-frame\?latest=1&consume=1/,
  "the preview requests only the newest captured frame and consumes stale queued frames");
assert.doesNotMatch(scanViewSource, /liveCloudQueue|smoothLivePreviewDepth/,
  "the preview does not cache or blend previous point clouds");
assert.doesNotMatch(appJs, /new MutationObserver\(stopPreviewOutsideScanning\)\.observe\(liveCamera/,
  "changing the live-preview button state must not synthesize a second toggle click");
assert.doesNotMatch(appJs, /livePreviewController\?\.stop\(\)|stopPreviewOutsideScanning/,
  "workspace-tab changes do not stop the live camera preview");
assert.match(appJs, /hasPersistentPointCloud:\(file,scan\)=>state\.objects\.some/);
assert.match(appJs, /startsWith\(`\$\{scan\.preview_subfolder\}_`\)/,
  "a completed capture recognizes the imported fusion from the same scan run");
assert.match(appJs, /showCapturePreview\(scan,\{open:captureStarted\}\)/);
assert.match(appJs, /import_point_cloud:\['Облако точек','cloud'\]/);
assert.match(appJs, /\.model-toolbar \.cad3-tool/);
console.log("scan view controls passed");
}
verifyScanView().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

(async () => {
  let apiState = { state: "idle" };
  const previewCalls = [];
  const cameraButton = element();
  const captureButton = element();
  sandbox.window.cadyxSyncCaptureButton = () => {
    const locked = captureButton.dataset.livePreviewLock === "true"
      || captureButton.dataset.scanCaptureBusy === "true"
      || captureButton.dataset.scanCapturePending === "true";
    captureButton.dataset.captureLocked = String(locked);
    captureButton.setAttribute("aria-disabled", String(locked));
  };
  const liveViewCalls = [];
  const liveController = sandbox.window.CadyxScanView.initializeLivePreview({
    button: cameraButton,
    captureButton,
    view: {
      showLivePreview(options) { liveViewCalls.push(["show", options, workspaceGrid.dataset.view]); },
      closeLivePreview() { liveViewCalls.push(["close"]); },
    },
  }, undefined, async (url, options = {}) => {
    previewCalls.push({ url, method: options.method || "GET" });
    if (options.method === "POST") apiState = { state: "starting" };
    if (options.method === "DELETE") apiState = { state: "idle" };
    return { ok: true, async json() { return { preview: apiState }; } };
  });
  const duplicateLiveController = sandbox.window.CadyxScanView.initializeLivePreview({
    button: cameraButton,
    captureButton,
    view: {
      showLivePreview() { liveViewCalls.push(["duplicate-show"]); },
      closeLivePreview() { liveViewCalls.push(["duplicate-close"]); },
    },
  });
  assert.equal(duplicateLiveController, liveController, "one camera button owns only one preview poller");
  await new Promise(resolve => setImmediate(resolve));
  await cameraButton.listeners.click();
  assert.equal(captureButton.dataset.captureLocked, "true", "live preview prevents a concurrent saved 360 capture");
  assert.equal(liveViewCalls.at(-1)[0], "show");
  assert.equal(liveViewCalls.at(-1)[1].open, true);
  await cameraButton.listeners.click();
  assert.equal(captureButton.dataset.captureLocked, "false", "stopping live preview re-enables 360 capture");
  assert.equal(liveViewCalls.at(-1)[0], "close");
  assert.deepEqual(previewCalls.map(call => call.method), ["GET", "POST", "DELETE"]);
  assert.equal(cameraButton.attributes["aria-pressed"], "false");
  await liveController.poll();
  apiState = { state: "running" };
  workspaceGrid.dataset.view = "overview";
  const requestsBeforeHiding = previewCalls.length;
  await liveController.poll();
  assert.equal(previewCalls.length, requestsBeforeHiding,
    "hidden scanner panels do not poll the device-status endpoint");
  assert.equal(previewCalls.at(-1).method, "GET",
    "changing workspace tabs never sends a camera-stop request");
  assert.equal(liveViewCalls.at(-1)[0], "close",
    "an active live scan is hidden from the viewport when Overview becomes active");
  assert.equal(liveViewCalls.some(call => call[0] === "show" && call[2] !== "scanning"), false,
    "live scan points are never displayed outside the Scanning tab");
  workspaceGrid.dataset.view = "scanning";
  await liveController.poll();
  assert.equal(liveViewCalls.at(-1)[0], "show",
    "returning to Scanning resumes displaying the still-running camera stream");
  await cameraButton.listeners.click();
  assert.equal(previewCalls.at(-1).method, "DELETE",
    "the Preview button remains the control that stops the live camera");
  liveController.dispose();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
