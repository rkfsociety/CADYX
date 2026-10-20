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
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply") }, URL, URLSearchParams };
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

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
assert.match(html, /id="viewport-live-scan"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="viewport-scan-mesh"[^>]*>Сетка<\/button>/);
assert.match(html, /id="viewport-scan-360"[^>]*>Сканировать 360°<\/button>/);
assert.ok(html.indexOf('id="viewport-live-scan"') < html.indexOf('id="viewport-scan-360"'));
assert.doesNotMatch(html, /live-scan-tabs|live-scan-cloud-tab|live-scan-mesh-tab/);
assert.match(html, /viewport\[data-scan-preview="true"\]\s*>\s*#model-preview/);
console.log("scan view controls passed");
