const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/ply-preview.js"), "utf8");
const assemblyEditor = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
assert.match(assemblyEditor, /selectAssemblyOccurrence\(pathId,instanceId,event\)/,
  "assembly occurrences are selectable from the tree and viewport");
assert.match(assemblyEditor, /owner_type:'human'/,
  "browser assembly edits acquire a human lease");
assert.match(assemblyEditor, /expected_revision:revision/,
  "browser assembly edits submit the inspected revision");
assert.match(assemblyEditor, /data-assembly-undo/);
assert.match(assemblyEditor, /cad3-assembly-select\{flex:1;min-width:0;grid-template-columns:18px minmax\(0,1fr\)\}/,
  "assembly rows do not reserve an empty grid column ahead of the full instance name");
assert.match(assemblyEditor, /cad3-assembly-select \.cad-tree-label\{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;/,
  "full assembly instance names wrap instead of inheriting one-line ellipsis");
assert.match(assemblyEditor, /previewAssemblyTransform\(/,
  "mouse manipulation updates an isolated viewport preview");
assert.match(assemblyEditor, /data-gizmo-kind/,
  "the viewport exposes axis and rotation manipulators");
assert.match(assemblyEditor, /kinematics\.redundant_mate_ids\?\.length/,
  "the assembly inspector exposes solver redundancy diagnostics");
assert.match(assemblyEditor, /pickAssemblyGeometryFace\(/,
  "viewport face selection resolves against read-only OCCT topology");
assert.match(assemblyEditor, /assemblyPickKind==='vertex'/,
  "the editor offers topological vertex picking for point constraints");
assert.match(assemblyEditor, /entity_kind:'vertex'/,
  "selected B-rep vertices produce typed persistent references");
assert.match(assemblyEditor, /data-assembly-pick-kind/,
  "the geometry picker switches between faces and vertices");
assert.match(assemblyEditor, /createAssemblyMateFromFaces\(/,
  "two selected geometry references create a persisted assembly mate");
for (const relation of ["parallel", "perpendicular", "distance", "angle"]) {
  assert.match(assemblyEditor, new RegExp(`value="${relation}"`),
    `${relation} can be selected for face based assembly constraints`);
}
assert.match(assemblyEditor, /cadyx:project-lock/,
  "the assembly editor reacts to AI and human lock state");
assert.match(assemblyEditor, /finishAssemblyGizmo\(\{pointerId\},false\)/,
  "an AI edit cancels and clears an in-flight human drag preview");

function appFunction(name, nextMarker) {
  const start = assemblyEditor.indexOf(`    async function ${name}(`);
  const end = assemblyEditor.indexOf(nextMarker, start);
  assert.ok(start >= 0 && end > start, `${name} implementation is present`);
  return assemblyEditor.slice(start, end).trim();
}

async function testAssemblyDragAndHistory() {
  const commits = [], clears = [];
  const drag = { pointerId: 4, instance: { id: "instance:a" }, moved: true,
    currentLocal: { translation: [0, 0, 0], rotation: [1, 0, 0, 0] },
    nextLocal: { translation: [2, 0, 0], rotation: [1, 0, 0, 0] } };
  const dragSandbox = { assemblyDrag: drag,
    commitAssemblyTransform: async (...args) => commits.push(args),
    clearAssemblyPreview: () => clears.push(true) };
  vm.runInNewContext(`${appFunction("finishAssemblyGizmo", "    gizmoRoot.addEventListener")}; this.finish=finishAssemblyGizmo;`, dragSandbox);
  await dragSandbox.finish({ pointerId: 4 }, true);
  assert.equal(commits.length, 1, "pointerup commits a changed assembly pose");
  assert.equal(clears.length, 1, "committed drag clears its transient preview");
  assert.equal(dragSandbox.assemblyDrag, null);

  dragSandbox.assemblyDrag = { ...drag, pointerId: 5 };
  await dragSandbox.finish({ pointerId: 5 }, false);
  assert.equal(commits.length, 1, "pointercancel discards the drag without a command");
  assert.equal(clears.length, 2, "cancelled drag clears its transient preview");

  const moveStart = assemblyEditor.indexOf("    function moveAssemblyGizmo(");
  const moveEnd = assemblyEditor.indexOf("    async function finishAssemblyGizmo(", moveStart);
  assert.ok(moveStart >= 0 && moveEnd > moveStart);
  const moveDrag = { pointerId: 7, kind: "translate", mode: "local", index: 0,
    axisScreen: [10, 0], lengthSquared: 100, dpr: 1, startClientX: 0, startClientY: 0,
    startShownPose: { translation: [0, 0, 0], rotation: [1, 0, 0, 0] },
    shownPose: { translation: [0, 0, 0], rotation: [1, 0, 0, 0] },
    currentLocal: { translation: [0, 0, 0], rotation: [1, 0, 0, 0] },
    instance: { id: "instance:a" }, moved: false };
  const previews = [], moveSandbox = { assemblyDrag: moveDrag,
    props: { querySelector: () => ({ value: "1" }) }, state: { snapEnabled: true },
    localPoseFromMode: pose => pose,
    previewAssemblyTransform: (...args) => previews.push(args),
    lastOverlaySignature: "cached", syncOverlay: () => {} };
  vm.runInNewContext(`${assemblyEditor.slice(moveStart, moveEnd)}; this.move=moveAssemblyGizmo;`, moveSandbox);
  moveSandbox.move({ pointerId: 7, clientX: 25, clientY: 0, preventDefault() {} });
  assert.equal(moveDrag.nextLocal.translation[0], 3, "pointermove snaps translation to the configured step");
  assert.equal(previews.length, 1, "pointermove refreshes the viewport preview immediately");
  assert.equal(moveSandbox.lastOverlaySignature, null);

  moveDrag.kind = "rotate";
  moveDrag.index = 2;
  moveDrag.shownPose = { translation: [0, 0, 0], rotation: [1, 0, 0, 0] };
  moveDrag.startShownPose = { translation: [0, 0, 0], rotation: [1, 0, 0, 0] };
  moveSandbox.quatMultiply = (a, b) => [a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3], a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2], a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1], a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]];
  moveSandbox.props.querySelector = () => ({ value: "0" });
  moveSandbox.move({ pointerId: 7, clientX: 20, clientY: 0, preventDefault() {} });
  const firstRotation = moveDrag.nextLocal.rotation.slice();
  moveSandbox.move({ pointerId: 7, clientX: 20, clientY: 0, preventDefault() {} });
  moveDrag.nextLocal.rotation.forEach((value, index) => assert.ok(Math.abs(value - firstRotation[index]) < 1e-12,
    "repeated pointermove coordinates do not accumulate the same rotation twice"));
  moveDrag.mode = "global";
  const half = Math.SQRT1_2;
  moveDrag.startShownPose.rotation = [half, half, 0, 0];
  moveDrag.shownPose.rotation = moveDrag.startShownPose.rotation.slice();
  moveSandbox.move({ pointerId: 7, clientX: 40, clientY: 0, preventDefault() {} });
  const globalHalfAngle = 10 * Math.PI / 180;
  const expectedGlobalRotation = [Math.cos(globalHalfAngle) * half,
    Math.cos(globalHalfAngle) * half, Math.sin(globalHalfAngle) * half,
    Math.sin(globalHalfAngle) * half];
  moveDrag.nextLocal.rotation.forEach((value, index) => assert.ok(
    Math.abs(value - expectedGlobalRotation[index]) < 1e-12,
    "global rotation is applied in world coordinates from the gesture-start pose"));

  const undo = [{ undo: { type: "restore" }, redo: { type: "apply" } }], redo = [], calls = [];
  const historySandbox = { assemblyUndo: undo, assemblyRedo: redo,
    executeAssemblyCommand: async (command, options) => {
      calls.push([command, options]);
      if (options.historyAction === "undo") historySandbox.assemblyRedo.push(historySandbox.assemblyUndo.pop());
      else if (options.historyAction === "redo") historySandbox.assemblyUndo.push(historySandbox.assemblyRedo.pop());
    },
    setAssemblyStatus: () => {} };
  vm.runInNewContext(`${appFunction("travelAssemblyHistory", "    const quatMultiply")}; this.travel=travelAssemblyHistory;`, historySandbox);
  await historySandbox.travel("undo");
  assert.equal(calls[0][0].type, "restore");
  assert.equal(calls[0][1].historyAction, "undo");
  await historySandbox.travel("redo");
  assert.equal(calls[1][0].type, "apply");
  assert.equal(calls[1][1].historyAction, "redo");

  const mateId = "mate:limits", oldLimits = { angle_min_deg: null, angle_max_deg: null,
    offset_min_mm: null, offset_max_mm: null }, submitted = [];
  const limitFields = ["angle_min_deg", "angle_max_deg", "offset_min_mm", "offset_max_mm"];
  const limitValues = ["-90", "90", "", ""];
  const limitInputs = limitFields.map((field, index) => ({ dataset: { mateLimit: `${mateId}:${field}` }, value: limitValues[index] }));
  const limitSandbox = { state: { assembly: { mates: [{ id: mateId, kind: "revolute", angle_deg: 0,
      offset_mm: 0, orientation: [1, 0, 0, 0], limits: oldLimits }] } },
    props: { querySelector: () => null,
      querySelectorAll: selector => selector.includes("data-mate-limit") ? limitInputs : [] },
    CSS: { escape: value => value }, cloneJson: value => JSON.parse(JSON.stringify(value)),
    executeAssemblyCommand: async (...args) => submitted.push(args), setAssemblyStatus: () => {} };
  vm.runInNewContext(`${appFunction("applyAssemblyMate", "    async function removeAssemblyMate")}; this.apply=applyAssemblyMate;`, limitSandbox);
  await limitSandbox.apply({ id: "instance:a" }, mateId);
  assert.deepEqual(JSON.parse(JSON.stringify(submitted[0][0].limits)), { angle_min_deg: -90, angle_max_deg: 90,
    offset_min_mm: null, offset_max_mm: null }, "blank bounds become null in the saved mate command");
  assert.deepEqual(JSON.parse(JSON.stringify(submitted[0][1].history.undo.limits)), oldLimits,
    "the mate editor preserves previous bounds for Undo");

  const integratedRequests = [], api = { refresh: async () => {} };
  api.request = async (url, options = {}) => {
    integratedRequests.push([url, options]);
    if (!options.method) return { document: { revision: 3 }, lock: { state: "FREE" } };
    if (url.endsWith("/leases")) return { lease: { lease_id: "lease:integrated" } };
    if (url.endsWith("/commands")) return { history_entry: { result: {} } };
    return {};
  };
  const instance = { id: "instance:a", translation: [0, 0, 0], rotation: [1, 0, 0, 0] };
  const integrated = { assemblyDrag: { pointerId: 9, instance, moved: true,
      currentLocal: { translation: [0, 0, 0], rotation: [1, 0, 0, 0] },
      nextLocal: { translation: [4, 0, 0], rotation: [1, 0, 0, 0] } },
    state: { project: "project:test", rev: 3, assemblyCoordinateMode: "local", selectedInstancePath: [] },
    window: { cadyxApi: api }, crypto: { randomUUID: () => "drag-owner" },
    assemblyUndo: [], assemblyRedo: [], assemblyLastRevision: null,
    assemblyTransformTargets: (_instance, pose) => [{ ...pose, instance_id: instance.id }],
    assemblyInstance: () => instance, instancePose: pose => ({ translation: pose.translation.slice(), rotation: pose.rotation.slice() }),
    poseCompose: () => ({}), poseInverse: () => ({}), quatMultiply: () => [1, 0, 0, 0], quatInverse: () => [1, 0, 0, 0],
    resetAssemblyHistory: () => {}, setAssemblyStatus: () => {}, clearAssemblyPreview: () => {}, };
  vm.runInNewContext([
    appFunction("executeAssemblyCommand", "    async function pickAssemblyGeometryFace"),
    appFunction("commitAssemblyTransform", "    async function applyAssemblyTransform"),
    appFunction("finishAssemblyGizmo", "    gizmoRoot.addEventListener"),
    "this.finish=finishAssemblyGizmo;",
  ].join("\n"), integrated);
  await integrated.finish({ pointerId: 9 }, true);
  const savedTransform = JSON.parse(integratedRequests[2][1].body);
  assert.equal(savedTransform.command.type, "transform_assembly_instance");
  assert.deepEqual(savedTransform.command.translation, [4, 0, 0]);
  assert.equal(savedTransform.expected_revision, 3);
  assert.match(integratedRequests[3][0], /release$/,
    "pointer commit reaches Core through a lease and releases it");

  async function commandProtocol({ commandFails = false, revision = 12, lockState = "FREE" } = {}) {
    const requests = [], api = { refreshCount: 0, refresh: async () => { api.refreshCount++; } };
    api.request = async (url, options = {}) => {
      requests.push([url, options]);
      if (!options.method) return { document: { revision }, lock: { state: lockState } };
      if (url.endsWith("/leases")) return { lease: { lease_id: "lease:test" } };
      if (url.endsWith("/commands")) {
        if (commandFails) throw new Error("solver rejected movement");
        return { history_entry: { result: { instance_id: "instance:a" } } };
      }
      return { lock: { state: "FREE" } };
    };
    const sandbox = { state: { project: "project:test", rev: 12 }, window: { cadyxApi: api },
      crypto: { randomUUID: () => "test-owner" }, assemblyUndo: [], assemblyRedo: [],
      assemblyLastRevision: null, setAssemblyStatus: () => {}, resetAssemblyHistory() {}, };
    vm.runInNewContext(`${appFunction("executeAssemblyCommand", "    async function pickAssemblyGeometryFace")}; this.execute=executeAssemblyCommand;`, sandbox);
    const expectedError = commandFails ? /solver rejected movement/ : revision !== 12
      ? /изменилась в другом сеансе/ : lockState !== "FREE" ? /изменяет сборку/ : null;
    if (expectedError) await assert.rejects(sandbox.execute({ type: "transform_assembly_instances" }), expectedError);
    else await sandbox.execute({ type: "transform_assembly_instances" });
    return { requests, refreshCount: api.refreshCount };
  }
  const { requests } = await commandProtocol();
  assert.match(requests[0][0], /\?view=browser$/);
  assert.equal(JSON.parse(requests[1][1].body).owner_type, "human");
  assert.equal(JSON.parse(requests[2][1].body).expected_revision, 12);
  assert.match(requests[3][0], /\/lease%3Atest\/release$/);
  const { requests: failedRequests } = await commandProtocol({ commandFails: true });
  assert.ok(failedRequests.some(([url]) => url.endsWith("/lease%3Atest/release")),
    "command failure still releases its human lease");
  const stale = await commandProtocol({ revision: 13 });
  assert.equal(stale.requests.length, 1, "stale revision is rejected before acquiring a human lease");
  assert.equal(stale.refreshCount, 1, "stale revision refreshes the saved view");
  const locked = await commandProtocol({ lockState: "AI_LOCKED" });
  assert.equal(locked.requests.length, 1, "AI editing blocks mutation before human lease acquisition");
}

testAssemblyDragAndHistory().catch((error) => { console.error(error); process.exitCode = 1; });
const start = source.indexOf("    const positions = [], bodyPositions = []");
const end = source.indexOf("    currentModelViewportGeometry = viewportGeometry;", start);
assert.ok(start > 0 && end > start);
const body = { id: "body:part", type: "body", geometry: { kind: "extruded_profile" } };
const mesh = (id, x, instance = true) => ({
  object_id: id, ...(instance ? { source_body_id: body.id } : {}), type: "body",
  positions: [x, 0, 0, x + 1, 0, 0, x, 1, 0], triangles: [0, 1, 2], lines: [0, 1], points: [],
  face_triangle_ranges: [{ face_index: 3, start: 0, end: 1 }],
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
  assert.equal(sandbox.geometry.bodyVertexRanges.length, objects.length);
  assert.equal(sandbox.geometry.faceTriangleRanges.length, objects.length);
  assert.deepEqual(Array.from(sandbox.geometry.faceTriangleRanges, (range) => range.faceIndex),
    objects.map(() => 3));
  assert.deepEqual(Array.from(sandbox.geometry.bodyVertexRanges, (range) => range.objectId),
    objects.map((object) => object.object_id));
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
