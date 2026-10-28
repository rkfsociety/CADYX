const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const parserSandbox = {
  window: {},
  TextDecoder,
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/ply-parser.js"), "utf8"), parserSandbox);
const parse = parserSandbox.window.CadyxPlyPreview.parse;

function mesh(faceCount = 3) {
  const header = Buffer.from(`ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nelement face ${faceCount}\nproperty list uchar uint vertex_indices\nproperty list uchar float texcoord\nproperty uchar material\nend_header\n`);
  const body = Buffer.alloc(36 + faceCount * 23);
  [0, 0, 0, 1, 0, 0, 0, 1, 0].forEach((value, index) => body.writeFloatLE(value, index * 4));
  for (let face = 0; face < faceCount; face++) {
    const start = 36 + face * 23;
    body[start] = 3;
    [0, 1, 2].forEach((value, index) => body.writeUInt32LE(value, start + 1 + index * 4));
    body[start + 13] = 2;
    body.writeFloatLE(0.25, start + 14);
    body.writeFloatLE(0.75, start + 18);
    body[start + 22] = 7;
  }
  const bytes = Buffer.concat([header, body]);
  return { buffer: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), headerSize: header.length };
}

const small = mesh();
const result = parse(small.buffer);
assert.deepEqual(Array.from(result.triangles), [0, 1, 2, 0, 1, 2, 0, 1, 2]);
assert.deepEqual(Array.from(result.positions), [0, 0, 0, 1, 0, 0, 0, 1, 0]);
const large = mesh(36_002);
const sampled = parse(large.buffer);
assert.equal(sampled.faceCount, 36_002);
assert.equal(sampled.previewFaceCount, 18_001);
assert.ok(Array.from(sampled.triangles).every((index, offset) => index === offset % 3));
assert.throws(() => parse(large.buffer.slice(0, -2)), /обрезаны/);
assert.throws(() => parse(small.buffer.slice(0, small.headerSize + 35)), /обрезаны/);
const invalid = small.buffer.slice(0);
new DataView(invalid).setUint32(small.headerSize + 37, 99, true);
assert.throws(() => parse(invalid), /отсутствующую вершину/);

const timers = [];
const browserSandbox = {
  window: {},
  URLSearchParams,
  Math,
  performance: { now: () => 1_000 },
};
browserSandbox.window.window = browserSandbox.window;
browserSandbox.window.setTimeout = (callback) => {
  timers.push(callback);
  return timers.length;
};
browserSandbox.window.fetch = async () => { throw new Error("unexpected fetch"); };
for (const filename of ["trace-draft.js", "browser-api.js", "viewport-helpers.js", "browser-state.js", "browser-viewers.js"]) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../web/${filename}`), "utf8"), browserSandbox);
}

const helpers = browserSandbox.window.CadyxViewportHelpers;
assert.equal(helpers.isRotationMatrix(helpers.cameraMatrixFromYawPitch(0.4, 0.2)), true);
assert.equal(helpers.CAMERA_PRESETS.front.yaw, Math.PI / 2,
  "the front preset looks along the turntable hinge axis");
assert.equal(helpers.CAMERA_PRESETS.right.yaw, 0,
  "the right preset shows the side opposite the requested front-facing hinge view");
const edges = helpers.buildFeatureEdges([0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
assert.deepEqual(Array.from(edges.featureEdges), [0, 1, 1, 2, 2, 0]);
const raster = helpers.rasterTriangleSetup(1, 1, 5, 1, 1, 5, 10, 10);
assert.equal(raster.minX, 1);
assert.equal(raster.maxY, 5);

const state = browserSandbox.window.CadyxBrowserState;
assert.equal(state.selectedResourceFromSearch("?folder=archive"), "folder:archive");
const progressWork = state.currentProgressWork({
  workflow: { phases: [{ id: "base", title: "Строит корпус", state: "in_progress" }] },
  tasks: [{ id: "profile", title: "Исправляет профиль", state: "running" }],
}, [{ state: "completed", action: "Получает изображение модели" }], { state: "FREE" });
assert.equal(progressWork.isWorking, true,
  "A reported running task remains visible after the last command finishes or the host restarts");
assert.equal(progressWork.dashboardWork.title, "Исправляет профиль");
assert.equal(progressWork.activity.action, "Получает изображение модели");
const lock = state.reduceAgentWorkLock({ active: false, revision: 2, projectIds: [] }, true, 3, ["project-1"]);
assert.equal(JSON.stringify(lock), JSON.stringify({ active: true, revision: 3, projectIds: ["project-1"] }));

const viewerModule = browserSandbox.window.CadyxBrowserViewers;
const presetButtons = ["isometric", "front"].map((cameraPreset) => ({
  dataset: { cameraPreset },
  pressed: null,
  setAttribute(name, value) {
    if (name === "aria-pressed") this.pressed = value;
  },
}));
const root = {
  querySelectorAll: () => presetButtons,
  addEventListener: () => {},
};
const canvas = { addEventListener: () => {} };
let appliedCamera = null;
const presetViewer = {
  getCamera: () => ({ zoom: 2.5 }),
  setCamera: (camera) => { appliedCamera = camera; },
};
const presetController = viewerModule.createCameraPresetController({ root, canvas, viewer: presetViewer });
assert.equal(presetController.apply("front"), true);
assert.equal(appliedCamera.yaw, Math.PI / 2);
assert.equal(appliedCamera.pitch, 0);
assert.equal(appliedCamera.zoom, 2.5);
assert.equal(appliedCamera.projection, "orthographic");
assert.equal(presetButtons[1].pressed, "true");
assert.equal(presetController.apply("right"), true);
assert.equal(appliedCamera.yaw, 0);
assert.equal(presetButtons[1].pressed, "false");
assert.equal(presetController.apply("missing"), false);

let captures = 0;
let publishes = 0;
const context = { projectId: "project-1", renderedProjectId: "project-1", revision: 7 };
const synchronizer = state.createViewportSnapshotSynchronizer({
  now: () => 1_000,
  setTimer: (callback) => {
    timers.push(callback);
    return timers.length;
  },
  capture: async () => {
    captures += 1;
    return "png";
  },
  getContext: () => context,
  publish: async () => {
    publishes += 1;
    return { ok: true, status: 200 };
  },
});
synchronizer.markDirty();
synchronizer.markDirty();
assert.equal(timers.length, 1);
timers.shift()();

const jsonResponse = (status, value) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: (name) => name.toLowerCase() === "content-type" ? "application/json" : null },
  json: async () => value,
});

setImmediate(async () => {
  // Exercise the actual form handlers without starting the viewport or touching saved projects.
  const source = fs.readFileSync(path.join(__dirname, "../web/ply-preview.js"), "utf8");
  const startupSource = source.slice(source.indexOf("const initialAddress ="), source.indexOf("const primaryGrid ="));
  for (const [url, chooser] of [
    ["http://localhost/", true],
    ["http://localhost/?view=projects", true],
    ["http://localhost/p/housing", false],
    ["http://localhost/?project_id=saved-project", false],
    ["http://localhost/?folder=scans", false],
  ]) {
    assert.equal(vm.runInNewContext(`${startupSource}\nprojectsChooserPinned`, {
      URLSearchParams, window: { location: new URL(url) },
    }), chooser, url);
  }
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", hidden: false, disabled: false, handlers: {},
      addEventListener(type, callback) { this.handlers[type] = callback; },
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
    });
    return elements.get(id);
  };
  const requests = [];
  const navigations = [];
  let completeRequest;
  const formSandbox = {
    $: element, encodeURIComponent,
    window: { location: { assign: (url) => navigations.push(url) } },
    navigateToWorkspaceProject: async projectId => {
      assert.equal(element("#create-project-dialog").open, false,
        "successful creation closes the dialog before navigation");
      assert.equal(element("#new-project-name").value, "");
      navigations.push(projectId);
    },
    api: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => { completeRequest = { resolve, reject }; });
    },
  };
  vm.runInNewContext(`let projectCreationPending = false;\n${source.slice(
    source.indexOf('$("#create-project").addEventListener'),
    source.indexOf('$("#create-project-group").addEventListener'),
  )}`, formSandbox);
  const submit = () => element("#create-project-form").handlers.submit({ preventDefault() {} });
  element("#create-project").handlers.click();
  assert.equal(element("#create-project-dialog").open, true);
  element("#cancel-create-project").handlers.click();
  assert.equal(element("#create-project-dialog").open, false);
  element("#new-project-name").value = "   ";
  await submit();
  assert.equal(requests.length, 0);
  element("#create-project").handlers.click();
  element("#new-project-name").value = "  Новый корпус  ";
  const pending = submit();
  await submit();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/projects");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(JSON.parse(requests[0].options.body).name, "Новый корпус");
  assert.equal(element("#submit-create-project").disabled, true);
  let cancelPrevented = false;
  element("#create-project-dialog").handlers.cancel({ preventDefault() { cancelPrevented = true; } });
  assert.equal(cancelPrevented, true);
  completeRequest.reject(new Error("Нет связи"));
  await pending;
  assert.equal(element("#new-project-name").value, "  Новый корпус  ");
  assert.equal(element("#create-project-dialog").open, true);
  assert.equal(element("#create-project-error").hidden, false);
  assert.match(element("#create-project-error").textContent, /Нет связи/);
  assert.equal(element("#submit-create-project").disabled, false);
  const retry = submit();
  completeRequest.resolve({ project: { id: "new-project" } });
  await retry;
  assert.deepEqual(navigations, ["new-project"],
    "creation delegates navigation to the existing canonical project router");
  assert.equal(element("#create-project-error").hidden, true);

  assert.equal(element("#create-project-dialog").open, false);
  await submit();
  assert.equal(requests.length, 2, "the completed form cannot create another identical project");

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(captures, 1);
  assert.equal(publishes, 1);

  const browserApi = browserSandbox.window.CadyxBrowserApi;
  const success = await browserApi.requestJson(
    "/ok",
    {},
    async () => jsonResponse(200, { ok: true }),
  );
  assert.equal(success.ok, true);
  await assert.rejects(
    () => browserApi.requestJson(
      "/missing",
      {},
      async () => jsonResponse(404, { error: "NOT_FOUND" }),
    ),
    (error) => error.status === 404
      && error.code === "NOT_FOUND"
      && error.message === "Проект, файл или объект не найден.",
  );

  console.log("PLY preview and browser modules: parser, helpers, API errors, viewer presets, state and snapshot throttling passed.");
});
