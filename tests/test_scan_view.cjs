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
let markerPointCount = 0;
for (const frame of liveRawFrames) {
  markerPointCount = 0;
  for (const [centerX, centerY] of [[200, 200], [440, 180], [320, 320]]) {
    for (let y = centerY - 16; y <= centerY + 16; y += 1) for (let x = centerX - 16; x <= centerX + 16; x += 1) {
      if ((x - centerX) ** 2 + (y - centerY) ** 2 > 16 ** 2) continue;
      const pixel = y * 640 + x;
      if (pixel % 2) continue;
      const depth = Math.round(100 + 0.12 * (x - 320) - 0.08 * (y - 200));
      frame[pixel * 2] = depth & 0xff;
      frame[pixel * 2 + 1] = depth >> 8;
      markerPointCount += 1;
    }
  }
}
let liveFrameSequence = 0;
const fetchPreview = async url => {
  if (url.includes("point-cloud?")) return { ok: true, async text() { return url.includes("origin.ply") ? originCloud : pointCloud; } };
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
await new Promise(resolve => setTimeout(resolve, 10));
assert.equal(viewport.dataset.scanPreview, "true", "live scanner frames are rendered in the main preview canvas");
assert.equal(viewport.dataset.scanPreviewMode, "live");
assert.equal(viewerLoads.at(-1).geometry.positions.length, markerPointCount * 3);
assert.ok(Math.abs(viewerLoads.at(-1).geometry.bounds.min[2]) < 2 && Math.abs(viewerLoads.at(-1).geometry.bounds.max[2]) < 2,
  "sparse table markers define XY and lie on that plane after alignment");
assert.deepEqual([...viewerLoads.at(-1).geometry.orbitCenter], [0, 0, 0],
  "the calibrated center-pixel ray intersection is the table origin even without a marker at the center");
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

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const scannerHtml = fs.readFileSync(path.join(__dirname, "../web/scanner.html"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
assert.match(html, /id="viewport-live-scan"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="viewport-scan-mesh"[^>]*>Сетка<\/button>/);
assert.match(html, /id="viewport-scan-360"[^>]*>Сканировать 360°<\/button>/);
assert.match(html, /id="viewport-live-camera"[^>]*>Сканер<\/button>/);
assert.ok(html.indexOf('id="viewport-live-scan"') < html.indexOf('id="viewport-scan-360"'));
assert.doesNotMatch(html, /live-scan-tabs|live-scan-cloud-tab|live-scan-mesh-tab/);
assert.doesNotMatch(html, /live-scan-layer|live-scan-frame|<iframe/);
assert.match(appJs, /viewer:modelViewer/);
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
  await new Promise(resolve => setImmediate(resolve));
  await cameraButton.listeners.click();
  assert.equal(captureButton.disabled, true, "live preview prevents a concurrent saved 360 capture");
  assert.equal(liveViewCalls.at(-1)[0], "show");
  assert.equal(liveViewCalls.at(-1)[1].open, true);
  await cameraButton.listeners.click();
  assert.equal(captureButton.disabled, false, "stopping live preview re-enables 360 capture");
  assert.equal(liveViewCalls.at(-1)[0], "close");
  assert.deepEqual(previewCalls.map(call => call.method), ["GET", "POST", "DELETE"]);
  assert.equal(cameraButton.attributes["aria-pressed"], "false");
  await liveController.poll();
  liveController.dispose();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
