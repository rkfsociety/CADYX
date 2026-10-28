const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/ply-preview.js"), "utf8");
const start = source.indexOf("    const positions = [], bodyPositions = []");
const end = source.indexOf("    currentModelViewportGeometry = viewportGeometry;", start);
assert.ok(start > 0 && end > start);
const body = { id: "body:part", type: "body", geometry: { kind: "extruded_profile" } };
const mesh = (id, x, instance = true) => ({
  object_id: id, ...(instance ? { source_body_id: body.id } : {}), type: "body",
  positions: [x, 0, 0, x + 1, 0, 0, x, 1, 0], triangles: [0, 1, 2], lines: [0, 1], points: [],
});
for (const objects of [[mesh("instance:a", 0), mesh("instance:b", 10)], [mesh(body.id, 0, false)]]) {
  const sandbox = {
    scene: { revision: 1, objects }, objectsById: new Map([[body.id, body]]),
    visibleSketchId: null, visibleObjects: [], transition: null,
    containsRevolvedProfile: () => false,
  };
  vm.runInNewContext(`${source.slice(start, end)}\nthis.geometry = viewportGeometry;`, sandbox);
  assert.equal(sandbox.geometry.triangles.length, objects.length * 3);
  assert.equal(sandbox.geometry.bodyTriangleRanges.length, objects.length);
  assert.deepEqual(Array.from(sandbox.geometry.bodyTriangleRanges, (range) => range.objectId),
    objects.map((object) => object.object_id));
  assert.equal(sandbox.geometry.bounds.max[0], objects.length === 2 ? 11 : 1);
  const elements = new Map();
  sandbox.$ = (id) => {
    if (!elements.has(id)) elements.set(id, {});
    return elements.get(id);
  };
  sandbox.integerFormat = new Intl.NumberFormat("en-US");
  sandbox.selectedProjectId = "project:test";
  sandbox.getProject = () => ({ project: { name: "Assembly" } });
  vm.runInNewContext(source.slice(source.indexOf("function renderSceneMetrics("),
    source.indexOf("function getProject(")), sandbox);
  sandbox.renderSceneMetrics(sandbox.scene, sandbox.objectsById, null);
  assert.equal(elements.get("#viewport-object-count").textContent, String(objects.length));
  assert.equal(elements.get("#viewport-face-count").textContent, String(objects.length));
}
console.log("Assembly and single-body viewport geometry and metrics passed.");
