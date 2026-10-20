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
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply") }, URL, URLSearchParams, setTimeout, clearTimeout };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/scan-view.js"), "utf8"), sandbox);

const cloudButton = element();
const meshButton = element();
const viewport = element();
const layer = element();
layer.hidden = true;
const frame = element();
const view = sandbox.window.CadyxScanView.initialize({ cloudButton, meshButton, viewport, layer, frame, meshReady: true });
assert.equal(layer.hidden, true);
cloudButton.click();
assert.equal(layer.hidden, false);
assert.equal(cloudButton.attributes["aria-pressed"], "true");
assert.equal(viewport.dataset.scanPreview, "true");
assert.equal(new URL(frame.src).searchParams.get("cloud_file"), "frame.ply");
assert.equal(new URL(frame.src).searchParams.get("project_id"), "project-1");
meshButton.click();
assert.equal(layer.dataset.mode, "mesh");
assert.equal(frame.hidden, false, "both modes keep the same embedded scan window visible");
assert.equal(viewport.dataset.scanPreview, "true");
assert.equal(cloudButton.attributes["aria-pressed"], "false");
assert.equal(meshButton.attributes["aria-pressed"], "true");
assert.equal(frame.messages.at(-1).message.mode, "mesh");
assert.match(frame.title, /Треугольная сетка/);
cloudButton.click();
assert.equal(layer.dataset.mode, "cloud");
assert.equal(frame.hidden, false);
assert.equal(frame.messages.at(-1).message.mode, "cloud");
assert.match(frame.title, /Облако точек/);
view.setOpen(false);
assert.equal(viewport.dataset.scanPreview, "false");
view.setOpen(true);
assert.equal(layer.dataset.mode, "cloud", "reopening preserves the selected scan view");
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(layer.hidden, false);
assert.equal(new URL(frame.src).searchParams.get("cloud_file"), "fused.ply");
assert.equal(new URL(frame.src).searchParams.get("cloud_kind"), "provisional_fusion");
meshButton.click();
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(layer.dataset.mode, "mesh", "status polls for the same scan preserve the selected view");
const messageCount = frame.messages.length;
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(frame.messages.length, messageCount, "polling an unchanged capture does not resend the same preview mode");
view.showLivePreview({ open: true });
assert.equal(layer.hidden, false);
assert.equal(layer.dataset.mode, "live");
assert.equal(frame.title, "Предпросмотр сканера в реальном времени");
assert.equal(new URL(frame.src).searchParams.get("live_only"), "1");
view.showCapture({ preview_folder: "scan folder", preview_file: "new-saved-capture.ply", preview_kind: "single" });
assert.equal(new URL(frame.src).searchParams.get("live_only"), "1", "360 status polling does not replace active live preview");
view.closeLivePreview();
assert.equal(layer.hidden, true, "stopping the live preview closes the shared viewport");

const blockedCloudButton = element();
const blockedMeshButton = element();
const blockedLayer = element();
blockedLayer.hidden = true;
blockedLayer.dataset.mode = "cloud";
const blocked = sandbox.window.CadyxScanView.initialize({
  cloudButton: blockedCloudButton, meshButton: blockedMeshButton, viewport: element(),
  layer: blockedLayer, frame: element(), meshReady: false,
});
assert.equal(blockedMeshButton.disabled, true, "mesh stays disabled until point cloud processing is confirmed");
blockedCloudButton.click();
assert.equal(blockedLayer.hidden, false, "the cloud control opens one preview while mesh is unavailable");
blockedCloudButton.click();
assert.equal(blockedLayer.hidden, true, "closing the cloud preview does not generate a mesh");
blockedMeshButton.click();
blocked.setOpen(true);
blocked.setMode("mesh");
assert.equal(blockedLayer.hidden, false);
assert.equal(blockedLayer.dataset.mode, "cloud");

const polledLayer = element();
polledLayer.hidden = true;
const polledFrame = element();
const polled = sandbox.window.CadyxScanView.initialize({
  cloudButton: element(), meshButton: element(), viewport: element(),
  layer: polledLayer, frame: polledFrame, meshReady: false,
});
const capture = { preview_folder: "scan folder", preview_file: "frame-1.ply", preview_kind: "single" };
polled.showCapture(capture);
assert.equal(polledLayer.hidden, true, "background scan polling does not open a closed preview");
polled.showCapture(capture, { open: true });
assert.equal(polledLayer.hidden, false, "the explicit scan start can open the preview");
polled.setOpen(false);
polled.showCapture({ ...capture, preview_file: "frame-2.ply" });
assert.equal(polledLayer.hidden, true, "new frames update without reopening a preview the user closed");
assert.equal(new URL(polledFrame.src).searchParams.get("cloud_file"), "frame-2.ply");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const scannerHtml = fs.readFileSync(path.join(__dirname, "../web/scanner.html"), "utf8");
assert.match(html, /id="viewport-live-scan"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="viewport-scan-mesh"[^>]*>Сетка<\/button>/);
assert.match(html, /id="viewport-scan-360"[^>]*>Сканировать 360°<\/button>/);
assert.match(html, /id="viewport-live-camera"[^>]*>Сканер<\/button>/);
assert.ok(html.indexOf('id="viewport-live-scan"') < html.indexOf('id="viewport-scan-360"'));
assert.doesNotMatch(html, /live-scan-tabs|live-scan-cloud-tab|live-scan-mesh-tab/);
assert.match(html, /viewport\[data-scan-preview="true"\]\s*>\s*#model-preview/);
assert.match(scannerHtml, /<a class="brand" href="\/" aria-label="CADYX — проекты"><svg class="brand-mark"/);
console.log("scan view controls passed");

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
