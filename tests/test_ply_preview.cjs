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
for (const filename of ["browser-api.js", "viewport-helpers.js", "browser-state.js", "browser-viewers.js"]) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../web/${filename}`), "utf8"), browserSandbox);
}

const helpers = browserSandbox.window.CadyxViewportHelpers;
assert.equal(helpers.isRotationMatrix(helpers.cameraMatrixFromYawPitch(0.4, 0.2)), true);
const edges = helpers.buildFeatureEdges([0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]);
assert.deepEqual(Array.from(edges.featureEdges), [0, 1, 1, 2, 2, 0]);
const raster = helpers.rasterTriangleSetup(1, 1, 5, 1, 1, 5, 10, 10);
assert.equal(raster.minX, 1);
assert.equal(raster.maxY, 5);

const state = browserSandbox.window.CadyxBrowserState;
assert.equal(state.selectedResourceFromSearch("?folder=archive"), "folder:archive");
const lock = state.reduceAgentWorkLock({ active: false, revision: 2, projectIds: [] }, true, 3, ["project-1"]);
assert.equal(JSON.stringify(lock), JSON.stringify({ active: true, revision: 3, projectIds: ["project-1"] }));

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

setImmediate(async () => {
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(captures, 1);
  assert.equal(publishes, 1);
  console.log("PLY preview and browser modules: parser, helpers, state and snapshot throttling passed.");
});
