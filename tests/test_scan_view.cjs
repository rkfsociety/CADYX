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
    listeners, attributes, dataset: {}, hidden: false, title: "", messages,
    contentWindow: { postMessage(message, origin) { messages.push({ message, origin }); } },
    get src() { return source; },
    set src(value) { source = value; attributes.src = value; },
    addEventListener(name, callback) { listeners[name] = callback; },
    setAttribute(name, value) { attributes[name] = value; },
    getAttribute(name) { return attributes[name] ?? null; },
    click() { listeners.click?.(); },
  };
};
const sandbox = { window: { location: new URL("http://127.0.0.1:8888/p/new-model-scan?project_id=project-1&scan_folder=scan%20folder&scan_file=frame.ply") }, URL, URLSearchParams };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/scan-view.js"), "utf8"), sandbox);

const button = element();
const layer = element();
layer.hidden = true;
const frame = element();
const cloudTab = element();
const meshTab = element();
const view = sandbox.window.CadyxScanView.initialize({ button, layer, frame, cloudTab, meshTab });
assert.equal(layer.hidden, true);
button.click();
assert.equal(layer.hidden, false);
assert.equal(button.attributes["aria-pressed"], "true");
assert.equal(new URL(frame.src).searchParams.get("cloud_file"), "frame.ply");
assert.equal(new URL(frame.src).searchParams.get("project_id"), "project-1");
meshTab.click();
assert.equal(layer.dataset.mode, "mesh");
assert.equal(frame.hidden, false, "both tabs keep the same embedded scan window visible");
assert.equal(meshTab.attributes["aria-selected"], "true");
assert.equal(frame.messages.at(-1).message.mode, "mesh");
assert.match(frame.title, /Треугольная сетка/);
cloudTab.click();
assert.equal(layer.dataset.mode, "cloud");
assert.equal(frame.hidden, false);
assert.equal(frame.messages.at(-1).message.mode, "cloud");
assert.match(frame.title, /Облако точек/);
button.click();
button.click();
assert.equal(layer.dataset.mode, "cloud", "reopening preserves the selected scan view");
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(layer.hidden, false);
assert.equal(frame.hidden, false);
assert.equal(new URL(frame.src).searchParams.get("cloud_file"), "fused.ply");
assert.equal(new URL(frame.src).searchParams.get("cloud_kind"), "provisional_fusion");
meshTab.click();
view.showCapture({ preview_folder: "scan folder", preview_file: "fused.ply", preview_kind: "provisional_fusion", preview_frame_count: 12 });
assert.equal(layer.dataset.mode, "mesh", "status polls for the same scan preserve the selected view");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
assert.match(html, /id="viewport-live-scan"[^>]*>Скан<\/button>/);
assert.match(html, /id="live-scan-cloud-tab"[^>]*>Облако точек<\/button>/);
assert.match(html, /id="live-scan-mesh-tab"[^>]*aria-controls="live-scan-frame"[^>]*>Сетка<\/button>/);
console.log("scan view tabs passed");
