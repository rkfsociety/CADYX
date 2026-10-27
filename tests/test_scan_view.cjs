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
const workspaceGrid = { dataset: { view: "scanning" } };
const testDocument = {
  getElementById: id => id === "scanner-map-alignment" ? mapAlignmentOutput : null,
  querySelector: selector => selector === "#primary-grid" ? workspaceGrid : null,
};
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply&scan_origin_file=origin.ply") }, document: testDocument, URL, URLSearchParams, setTimeout: scheduleTestTimeout, clearTimeout, fetch: async () => ({ ok: true, async text() { return pointCloud; } }) };
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

const pointCloud = "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nelement face 0\nend_header\n0 0 -120\n1 0 -119.8\n0 1 -120.1\n";
const originPoints = [];
for (let y = -35; y < 35; y += 1) for (let x = -35; x < 35; x += 1) originPoints.push(`${x} ${y} ${-120 + 0.2 * x - 0.1 * y}`);
const originCloud = `ply\nformat ascii 1.0\nelement vertex ${originPoints.length}\nproperty float x\nproperty float y\nproperty float z\nelement face 0\nend_header\n${originPoints.join("\n")}\n`;
const viewerLoads = [];
const viewer = { load(geometry, options) { viewerLoads.push({ geometry, options }); } };
const baseGeometry = { positions: new Float64Array([0, 0, 0]), triangles: new Uint32Array(0), bounds: { min: [0, 0, 0], max: [0, 0, 0] } };
const getModelGeometry = () => baseGeometry;
const fetchCloud = async url => ({ ok: true, async text() { assert.match(url, /point-cloud\?/); return pointCloud; } });
const liveRawFrames = [new Uint8Array(640 * 400 * 2), new Uint8Array(640 * 400 * 2)];
let liveRawFrameSource = liveRawFrames;
const centers = [[220, 180], [420, 180], [220, 220], [420, 220], [320, 200]];
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
let markerPointCount = 0;
for (const frame of liveRawFrames) {
  markerPointCount = 0;
  for (const [centerX, centerY] of centers) {
    for (let y = centerY - 16; y <= centerY + 16; y += 1) for (let x = centerX - 16; x <= centerX + 16; x += 1) {
      if ((x - centerX) ** 2 + (y - centerY) ** 2 > 16 ** 2) continue;
      const pixel = y * 640 + x;
      const depth = Math.round(130 + 0.12 * (x - 320) - 0.08 * (y - 200));
      frame[pixel * 2] = depth & 0xff;
      frame[pixel * 2 + 1] = depth >> 8;
      if (sandbox.window.CadyxScanView.isScanDepthValid(depth)) markerPointCount += 1;
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
assert.equal(cloudButton.attributes["aria-pressed"], "true", cloudButton.title);
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
assert.match(cloudButton.title, /Скрыть/);
view.setOpen(false);
assert.equal(viewport.dataset.scanPreview, "false");
assert.equal(viewerLoads.at(-1).geometry, baseGeometry, "closing the cloud restores the CAD model in the same canvas");
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
await waitUntil(() => viewerLoads.length >= livePreviewLoadStart + 3
  && viewerLoads.at(-1)?.geometry.points?.length === markerPointCount);
assert.equal(viewport.dataset.scanPreview, "true", "live scanner frames are rendered in the main preview canvas");
assert.equal(viewport.dataset.scanPreviewMode, "live");
assert.ok(viewerLoads.at(-1).geometry.pointLayers?.[0]?.points?.length > 0, cloudButton.title);
assert.equal(viewerLoads.at(-1).geometry.pointPreviewLimit, 8_000);
assert.equal(viewerLoads.at(-1).geometry.points.length, markerPointCount,
  "only the sparse table markers receive the preview highlight");
assert.equal(viewerLoads.at(-1).geometry.pointStyle.size, 3.5);
assert.equal(viewerLoads.at(-1).geometry.pointStyle.opacity, 1);
assert.equal(viewerLoads.at(-1).geometry.pointStyle.color, "#59d9ff");
const markerPositions = viewerLoads.at(-1).geometry.positions;
const markerCenter = [0, 0, 0];
const markerIndices = new Set(viewerLoads.at(-1).geometry.points);
let minimumObjectZ = Infinity;
for (let point = 0; point < markerPositions.length / 3; point += 1) {
  const z = markerPositions[point * 3 + 2];
  if (!markerIndices.has(point)) minimumObjectZ = Math.min(minimumObjectZ, z);
}
assert.ok(minimumObjectZ >= 0, "object points remain at or above the table-marker plane");
for (const pointIndex of viewerLoads.at(-1).geometry.points) {
  const index = pointIndex * 3;
  assert.ok(Math.abs(markerPositions[index + 2]) < 2,
    "table markers lie in the XY plane even when object points are off that plane");
  for (let axis = 0; axis < 3; axis += 1) markerCenter[axis] += markerPositions[index + axis] / markerPointCount;
}
assert.ok(Math.abs(markerCenter[0]) < 5 && Math.abs(markerCenter[1]) < 5,
  `table markers stay centered on the calibrated XY origin: ${markerCenter}`);
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0],
  "the mapped table-marker centroid is the preview origin");
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0]);
const firstTurntableCloud = viewerLoads.at(-1).geometry.positions.slice(0, 3);
const firstLiveFitRadius = viewerLoads.at(-1).geometry.fitRadius;
await new Promise(resolve => setTimeout(resolve, 380));
assert.ok(liveFrameSequence >= 2, "the live point cloud accepted a newer camera frame");
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0], "live frames keep the first-frame camera pivot");
assert.equal(viewerLoads.at(-1).geometry.fitRadius, firstLiveFitRadius, "live frames keep a fixed view scale");
assert.equal(viewerLoads.at(-1).options.reset, false, "new live frames never reset the camera");
const secondTurntableCloud = viewerLoads.at(-1).geometry.positions.slice(0, 3);
assert.deepEqual([...secondTurntableCloud], [...firstTurntableCloud],
"identical scanner frames are not rotated a second time using the table angle");
assert.ok(livePollDelays.includes(100), "idle frame polling adapts to the measured 250 ms camera cadence");
view.showCapture({ preview_folder: "scan folder", preview_file: "new-saved-capture.ply", preview_kind: "single" });
assert.equal(viewport.dataset.scanPreview, "true", "360 status polling does not replace the live canvas preview");
view.closeLivePreview();
assert.equal(viewport.dataset.scanPreview, "false", "stopping the live preview restores the CAD geometry");
assert.equal(viewport.dataset.scanPreviewMode, "");

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
const mapAlignedView = sandbox.window.CadyxScanView.initialize({
  cloudButton: element(), meshButton: element(), viewport: element(), viewer, getModelGeometry, meshReady: true,
}, undefined, fetchPreview);
const mapAlignedStart = viewerLoads.length;
mapAlignedView.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.length >= mapAlignedStart + 3
  && liveFrameSequence >= 2);
const mapAlignedGeometries = [viewerLoads[mapAlignedStart + 1].geometry, viewerLoads[mapAlignedStart + 2].geometry];
const nonMarkerCentroid = geometry => {
  const markers = new Set(geometry.points);
  const centroid = [0, 0, 0];
  let pointCount = 0;
  for (let point = 0; point < geometry.pointCloudVertexCount; point += 1) {
    if (markers.has(point)) continue;
    for (let axis = 0; axis < 3; axis += 1) centroid[axis] += geometry.positions[point * 3 + axis];
    pointCount += 1;
  }
  return centroid.map(value => value / pointCount);
};
const alignedObjectCentroids = mapAlignedGeometries.map(nonMarkerCentroid);
assert.ok(Math.hypot(...alignedObjectCentroids[0].map((value, axis) => value - alignedObjectCentroids[1][axis])) < 1,
  "re-registering each frame against the table map keeps points fixed in table coordinates as image-space markers move");
assert.match(mapAlignmentOutput.textContent, /Карта \d+\/5/,
  "the live preview reports its current table-map correspondence quality");
await waitUntil(() => liveFrameSequence >= 3);
await new Promise(resolve => setTimeout(resolve, 30));
assert.equal(viewerLoads.length, mapAlignedStart + 3,
  "a frame without enough table markers does not replace the last map-aligned cloud with unaligned points");
assert.match(mapAlignmentOutput.textContent, /последний кадр/,
  "the map status explains when the preview is retaining its last aligned frame");
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
const countValidFramePoints = frame => {
  const view = new DataView(frame.buffer);
  let count = 0;
  for (let pixel = 0; pixel < 640 * 400; pixel += 1) {
    if (sandbox.window.CadyxScanView.isScanDepthValid(view.getUint16(pixel * 2, true))) count += 1;
  }
  return count;
};
const displayedLiveCloudCount = geometry => Number.isInteger(geometry.pointCloudVertexCount)
  && geometry.pointCloudVertexCount > 0 ? geometry.pointCloudVertexCount
  : geometry.pointLayers?.[0]?.points?.length ?? geometry.pointCloudVertexCount;
const unalignedExpectedPointCount = countValidFramePoints(liveRawFrameSource[0]);
const secondUnalignedExpectedPointCount = countValidFramePoints(liveRawFrameSource[1]);
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
assert.equal(displayedLiveCloudCount(firstUnaligned), unalignedExpectedPointCount,
  "sparse depth frames render every valid return, including pixels outside the old preview lattice");
assert.deepEqual([...secondUnaligned.orbitCenter], [...firstUnaligned.orbitCenter],
  "unregistered live frames retain the first valid frame camera center");
assert.equal(secondUnaligned.fitRadius, firstUnaligned.fitRadius,
  "unregistered live frames retain the first valid frame camera scale");
assert.notDeepEqual([...secondUnaligned.bounds.min], [...firstUnaligned.bounds.min],
  "the stability assertion covers frames whose cloud bounds actually move");
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
assert.equal(displayedLiveCloudCount(movingSecondGeometry), countValidFramePoints(liveRawFrameSource[1]),
  "the latest valid moving frame replaces the previous point cloud without temporal blending");
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
const capture = { preview_folder: "scan folder", preview_file: "frame-1.ply", origin_file: "origin.ply", preview_kind: "single" };
polled.showCapture(capture);
assert.equal(polledCloudButton.attributes["aria-pressed"], "false", "background scan polling does not open a closed preview");
polled.showCapture(capture, { open: true });
await new Promise(resolve => setImmediate(resolve));
assert.equal(polledCloudButton.attributes["aria-pressed"], "true", "the explicit scan start opens the point cloud inside the main canvas");
polled.setOpen(false);
polled.showCapture({ ...capture, preview_file: "frame-2.ply" });
assert.equal(polledCloudButton.attributes["aria-pressed"], "false", "new frames update without reopening a preview the user closed");

const serverPointCloudButton = element();
const serverModelGeometry = {
  positions: new Float64Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), triangles: new Uint32Array(0),
  points: new Uint32Array([0, 1, 2]), bounds: { min: [0, 0, 0], max: [1, 1, 0] },
};
const serverPreview = sandbox.window.CadyxScanView.initialize({
  cloudButton: serverPointCloudButton, meshButton: element(), viewport: element(), viewer,
  getModelGeometry: () => serverModelGeometry,
  hasPersistentPointCloud: file => file === "merged.ply",
  meshReady: false,
}, undefined, fetchPreview);
serverPreview.showCapture({ ...capture, state: "running" }, { open: true });
await new Promise(resolve => setImmediate(resolve));
assert.equal(serverPointCloudButton.attributes["aria-pressed"], "true");
serverPreview.showCapture({ ...capture, state: "completed", preview_file: "merged.ply" });
assert.equal(serverPointCloudButton.disabled, true, "a persisted fusion stays in the CAD model viewer");
assert.equal(serverPointCloudButton.attributes["aria-pressed"], "false");
assert.equal(viewerLoads.at(-1).geometry, serverModelGeometry, "completion restores the server-rendered point-cloud body");
serverPointCloudButton.click();
assert.equal(viewerLoads.at(-1).geometry, serverModelGeometry, "the scan overlay cannot replace the persisted CAD cloud");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const scannerHtml = fs.readFileSync(path.join(__dirname, "../web/scanner.html"), "utf8");
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
assert.match(appJs, /if\(grid\?\.dataset\.view!=='scanning'\)livePreviewController\?\.stop\(\)/,
  "leaving the Scanning tab explicitly stops the camera preview through its controller");
assert.match(appJs, /hasPersistentPointCloud:file=>state\.objects\.some/);
assert.match(appJs, /showCapturePreview\(scan,\{open:captureStarted\}\)/);
assert.match(appJs, /import_point_cloud:\['Облако точек','cloud'\]/);
assert.match(scannerHtml, /<a class="brand" href="\/" aria-label="CADYX — проекты"><svg class="brand-mark"/);
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
  await liveController.poll();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(previewCalls.at(-1).method, "DELETE",
    "leaving Scanning stops the live camera through the API, without toggling its button");
  assert.equal(liveViewCalls.at(-1)[0], "close",
    "an active live scan is removed from the viewport when Overview becomes active");
  assert.equal(liveViewCalls.some(call => call[0] === "show" && call[2] !== "scanning"), false,
    "live scan points are never displayed outside the Scanning tab");
  liveController.dispose();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
