const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const traceDraftSource = fs.readFileSync(path.join(__dirname, "..", "web", "trace-draft.js"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "..", "web", "browser-api.js"), "utf8");
const window = {
  fetch: async () => { throw new Error("unexpected default fetch"); },
};
window.fetch.bind = Function.prototype.bind.bind(window.fetch);
const context = vm.createContext({ window, Error, JSON, Object, TypeError, Number, encodeURIComponent });
vm.runInContext(traceDraftSource, context, { filename: "trace-draft.js" });
vm.runInContext(source, context, { filename: "browser-api.js" });

const { TraceDraft, TraceEditorSession } = window.CadyxBrowserApi;
const draft = new TraceDraft({ segments: [] });
assert.equal(draft.canUndo, false);
assert.equal(draft.canRedo, false);

draft.update((value) => value.segments.push({ kind: "line", start: { x: 0, y: 0 }, end: { x: 1, y: 0 } }));
assert.equal(draft.snapshot().segments.length, 1);
assert.equal(draft.canUndo, true);
assert.equal(draft.canRedo, false);

const external = draft.snapshot();
external.segments[0].end.x = 99;
assert.equal(draft.snapshot().segments[0].end.x, 1, "snapshots must not mutate draft state");

assert.equal(draft.undo().segments.length, 0);
assert.equal(draft.canRedo, true);
assert.equal(draft.redo().segments.length, 1);

draft.undo();
draft.replace({ segments: [{ kind: "line", start: { x: 2, y: 2 }, end: { x: 3, y: 2 } }] });
assert.equal(draft.canRedo, false, "a new edit must clear redo history");
assert.equal(draft.snapshot().segments[0].start.x, 2);

const bounded = new TraceDraft([], { historyLimit: 4 });
const sharedSegment = { kind: "line", start: { x: 0, y: 0 }, end: { x: 1, y: 0 } };
for (let index = 0; index < 20; index += 1) bounded.update((segments) => segments.push(index ? { ...sharedSegment } : sharedSegment));
assert.equal(bounded.historySize, 4, "undo history must stay within the configured bound");
assert.equal(bounded.current().length, 20);
assert.equal(bounded.current(), bounded.current(), "current view must not allocate a new container");
const owned = bounded.snapshot();
owned[0].end.x = 77;
assert.equal(bounded.current()[0].end.x, 1, "ownership snapshots stay isolated from the live revision");

const longSegments = Array.from({ length: 4096 }, (_, index) => ({
  kind: "line",
  start: { x: index, y: 0 },
  end: { x: index + 1, y: 0 },
}));
const longDraft = new TraceDraft(longSegments, { historyLimit: 8 });
const retainedHead = longDraft.current()[0];
for (let index = 0; index < 64; index += 1) {
  longDraft.update((segments) => segments.push({
    kind: "line",
    start: { x: 4096 + index, y: 0 },
    end: { x: 4097 + index, y: 0 },
  }));
}
assert.equal(longDraft.historySize, 8, "4096-segment editing must retain only bounded undo revisions");
assert.equal(longDraft.current()[0], retainedHead, "unchanged long-trace segments must be structurally shared across edits");
assert.equal(longDraft.current().length, 4160);

(async () => {
  const session = new TraceEditorSession({
    projectId: "project/with space",
    leaseId: "lease-1",
    expectedRevision: 7,
    name: "Reference contour",
    sourceImageSha256: "ab".repeat(32),
    coordinateSystem: { origin: { x: 10, y: 20 }, units_per_pixel: 0.5, unit: "mm", y_axis: "down" },
    segments: [{ kind: "line", start: { x: 0, y: 0 }, end: { x: 1, y: 0 } }],
  });

  const calls = [];
  const response = {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => ({
      document: { revision: 8 },
      history_entry: { result: { trace_id: "trace-1", trace_version: 1, sketch_id: "sketch-1" } },
    }),
  };
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return response;
  };

  const saved = await session.save(fetchImpl);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/v1/projects/project%2Fwith%20space/commands");
  assert.equal(calls[0].options.method, "POST");
  const request = JSON.parse(calls[0].options.body);
  assert.equal(request.lease_id, "lease-1");
  assert.equal(request.expected_revision, 7);
  assert.equal(request.command.type, "save_traced_curve_version");
  assert.equal(request.command.base_version, 0);
  assert.equal(request.command.trace_id, undefined);
  assert.equal(saved.document.revision, 8);
  assert.equal(session.traceId, "trace-1");
  assert.equal(session.baseVersion, 1);
  assert.equal(session.expectedRevision, 8);

  const nextCommand = session.command();
  assert.equal(nextCommand.trace_id, "trace-1");
  assert.equal(nextCommand.base_version, 1);

  console.log("trace draft tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});