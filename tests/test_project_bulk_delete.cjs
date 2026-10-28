const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/ply-preview.js"), "utf8");
const handlers = new Map();
const elements = new Map();
const requests = [];
const notices = [];
let confirmed = false;
let confirmation;
let refreshes = 0;
let finish;
const sandbox = {
  projects: ["a", "b", "busy", "other"].map(id => ({ project: { id, name: id === "other" ? "Other" : "Duplicate" } })),
  $: id => {
    if (!elements.has(id)) elements.set(id, { value: "", addEventListener: (event, fn) => handlers.set(`${id}:${event}`, fn) });
    return elements.get(id);
  },
  canSelectProject: id => id !== "busy",
  renderProjects: () => vm.runInContext("updateProjectSelection()", context),
  window: { confirm: text => { confirmation = text; return confirmed; }, location: { assign: () => assert.fail("unexpected redirect") } },
  projectAddressPinned: false,
  selectedProjectId: null,
  encodeURIComponent,
  api: async url => {
    requests.push(url);
    if (url.endsWith("/a")) return new Promise(resolve => { finish = resolve; });
    throw new Error("Deletion denied");
  },
  notify: (text, error) => notices.push({ text, error }),
  refreshWorkspace: async () => { refreshes += 1; },
};
const context = vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf("const selectedProjectIds ="), source.indexOf("async function deleteProject(")), context);
(async () => {
  vm.runInContext("updateProjectSelection()", context);
  assert.equal(sandbox.$("#projects-selection-toolbar").hidden, true);
  vm.runInContext('selectedProjectIds.add("a"); updateProjectSelection();', context);
  assert.equal(sandbox.$("#projects-selection-toolbar").hidden, false, "the first checkbox reveals bulk actions");
  sandbox.$("#projects-search").value = "duplicate";
  handlers.get("#select-visible-projects:click")();
  assert.equal(sandbox.$("#projects-selection-count").textContent, "Выбрано: 2");
  const remove = handlers.get("#delete-selected-projects:click");
  await remove();
  assert.equal(requests.length, 0, "cancellation never deletes projects");
  assert.match(confirmation, /\(2\)/);
  confirmed = true;
  const pending = remove();
  assert.equal(sandbox.$("#delete-selected-projects").disabled, true);
  await remove();
  assert.equal(requests.length, 1, "repeated clicks cannot replay a running deletion");
  finish({ cleanup_pending: true });
  await pending;
  assert.deepEqual(requests, ["/api/v1/projects/a", "/api/v1/projects/b"]);
  assert.equal(refreshes, 1);
  assert.equal(sandbox.$("#projects-selection-count").textContent, "Выбрано: 1", "failed deletions remain selected");
  assert.match(notices[0].text, /1 из 2/);
  assert.match(notices[0].text, /Deletion denied/);
  assert.match(notices[0].text, /Очистка файлов/);
  assert.equal(notices[0].error, true);
  handlers.get("#clear-project-selection:click")();
  assert.equal(sandbox.$("#projects-selection-toolbar").hidden, true, "clearing the last selection hides bulk actions");
  assert.equal(sandbox.$("#delete-selected-projects").disabled, true);
  vm.runInContext('selectedProjectIds.add("busy"); selectedProjectIds.add("missing"); updateProjectSelection();', context);
  assert.equal(sandbox.$("#projects-selection-count").textContent, "Выбрано: 1", "removed projects are pruned from selection");
  await remove();
  assert.equal(requests.length, 2, "a project that becomes busy is never sent for deletion");
  assert.match(notices[1].text, /0 из 1/);
  console.log("Project bulk deletion: selection, filtering, cancellation, replay guard and partial failures passed.");
})().catch(error => { console.error(error); process.exitCode = 1; });
