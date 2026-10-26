const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const refreshSource = source.slice(source.indexOf('    let semanticRequest ='), source.indexOf('    let timer;const schedule='));
const groupsSource = source.slice(source.indexOf('    function modelGroups(){'), source.indexOf('    function renderTree(){'));
const treeSource = source.slice(source.indexOf('    function renderTree(){'), source.indexOf('    function renderProps('));

async function main() {
  let project = 'old';
  const pending = [];
  const state = { selected: null, objects: [] };
  const sandbox = {
    window: { cadyxSelectedProjectId: () => project },
    location: { search: '' }, URLSearchParams, detail: { hidden: false }, state,
    fetch: url => new Promise(resolve => pending.push({ url, resolve })),
    makeLabels() {}, clearSelection() {}, renderTree() {}, renderProps() {}, syncOverlay() {}, schedule() {},
    console: { debug() {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${refreshSource}\nglobalThis.refresh = refresh;`, sandbox);
  const oldRequest = sandbox.refresh();
  project = 'new';
  const newRequest = sandbox.refresh();
  function complete(prefix, ok = true) {
    for (const request of pending.filter(item => item.url.includes(`/projects/${prefix}`))) {
      request.resolve({ ok, status: ok ? 200 : 503, json: async () => request.url.endsWith('/viewport')
        ? { revision: 1, objects: [] }
        : { document: { revision: 1, objects: [{ id: prefix, type: 'body' }] } } });
    }
  }
  complete('new');
  await newRequest;
  assert.equal(state.project, 'new', 'slug selection must load without project_id query');
  complete('old');
  await oldRequest;
  assert.equal(state.project, 'new', 'late old-project response must not replace current project');
  project = 'failed';
  const failed = sandbox.refresh();
  complete('failed', false);
  await failed;
  assert.equal(state.project, 'new', 'HTTP failure must not commit invalid state');
  state.objects = [
    { id: 'stl', type: 'body' },
    { id: 'import', type: 'feature', body_id: 'stl', operation: 'import_stl' },
  ];
  vm.runInContext(`${groupsSource}\nglobalThis.groups = modelGroups;`, sandbox);
  assert.equal(sandbox.groups()[0].body.id, 'stl', 'imported STL must appear in the model tree');
  assert.match(treeSource, /disclosure\.innerHTML=`<span class="cad-tree-chevron">\$\{expanded\?'−':'\+'\}<\/span>`/, 'expanded sketch rows must show minus and collapsed rows plus');
  console.log('Panel state: slug selection, stale responses, HTTP failures and STL visibility passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
