const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "web", "browser-api.js"), "utf8");
const window = {
  fetch: async () => { throw new Error("fetch is not used by draft tests"); },
};
window.fetch.bind = Function.prototype.bind.bind(window.fetch);
const context = vm.createContext({ window, Error, JSON, Object, TypeError });
vm.runInContext(source, context, { filename: "browser-api.js" });

const { TraceDraft } = window.CadyxBrowserApi;
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

console.log("trace draft tests passed");
