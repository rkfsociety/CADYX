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
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply&scan_origin_file=origin.ply") }, URL, URLSearchParams, setTimeout, clearTimeout, fetch: async () => ({ ok: true, async text() { return pointCloud; } }) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/scan-view.js"), "utf8"), sandbox);
let sampledLivePixels = 0;
const liveSampleSlots = new Set();
for (let y = 0; y < 400; y += 1) {
  for (let x = 0; x < 640; x += 1) {
    const slot = sandbox.window.CadyxScanView.livePreviewSampleIndex(x, y);
    if (slot >= 0) { sampledLivePixels += 1; liveSampleSlots.add(slot); }
  }
}
assert.equal(sampledLivePixels, 48_000, "live preview uses a fixed 48,000-pixel lattice");
assert.equal(liveSampleSlots.size, 48_000, "each sampled pixel maps to a unique stable slot");
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(99.995), false);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(100), true);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(250), true);
assert.equal(sandbox.window.CadyxScanView.isScanDepthValid(250.005), false);
assert.equal(sandbox.window.CadyxScanView.smoothLivePreviewDepth(0, 100), 100);
assert.equal(sandbox.window.CadyxScanView.smoothLivePreviewDepth(100, 120), 115);

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
      if (sandbox.window.CadyxScanView.livePreviewSampleIndex(x, y) >= 0
          && sandbox.window.CadyxScanView.isScanDepthValid(depth)) markerPointCount += 1;
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
    return { ok: true, headers: { get() { return String(1000 + index); } }, async arrayBuffer() { return liveRawFrames[Math.min(index, 1)].buffer; } };
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
view.showLivePreview({ open: true });
await waitUntil(() => viewerLoads.at(-1)?.geometry.points?.length === markerPointCount);
assert.equal(viewport.dataset.scanPreview, "true", "live scanner frames are rendered in the main preview canvas");
assert.equal(viewport.dataset.scanPreviewMode, "live");
assert.ok(viewerLoads.at(-1).geometry.pointCloudVertexCount > 0, cloudButton.title);
assert.equal(viewerLoads.at(-1).geometry.pointPreviewLimit, 48_000);
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
view.showCapture({ preview_folder: "scan folder", preview_file: "new-saved-capture.ply", preview_kind: "single" });
assert.equal(viewport.dataset.scanPreview, "true", "360 status polling does not replace the live canvas preview");
view.closeLivePreview();
assert.equal(viewport.dataset.scanPreview, "false", "stopping the live preview restores the CAD geometry");
assert.equal(viewport.dataset.scanPreviewMode, "");

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
assert.match(html, /id="viewport-live-scan"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="viewport-scan-mesh"[^>]*>Сетка<\/button>/);
assert.match(html, /id="viewport-scan-360"[^>]*>Сканировать 360°<\/button>/);
assert.match(html, /id="viewport-live-camera"[^>]*>Предпросмотр<\/button>/);
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
assert.doesNotMatch(appJs, /new MutationObserver\(stopPreviewOutsideScanning\)\.observe\(liveCamera/,
  "changing the live-preview button state must not synthesize a second toggle click");
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
      showLivePreview(options) { liveViewCalls.push(["show", options]); },
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
  liveController.dispose();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
