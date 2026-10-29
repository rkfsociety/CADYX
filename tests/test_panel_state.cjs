const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const refreshSource = source.slice(source.indexOf('    let semanticRequest ='), source.indexOf('    let timer;const schedule='));
const groupsSource = source.slice(source.indexOf('    function modelGroups(){'), source.indexOf('    function renderTree(){'));
const treeSource = source.slice(source.indexOf('    function renderTree(){'), source.indexOf('    function renderProps('));
const selectionSource = source.slice(source.indexOf('    function select(id,reveal=false){'), source.indexOf('    function clearSelection(){'));

async function main() {
  let project = 'old';
  const state = { selected: null, objects: [] };
  const sandbox = {
    window: { cadyxSelectedProjectId: () => project },
    location: { search: '' }, URLSearchParams, detail: { hidden: false }, state,
    fetch: () => { throw new Error('semantic panels must reuse the loaded snapshot'); },
    makeLabels() {}, clearSelection() {}, renderTree() {}, renderProps() {}, syncOverlay() {}, schedule() {},
    console: { debug() {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${refreshSource}\nglobalThis.refresh = refresh;`, sandbox);
  const snapshot = id => ({ projectId: id, info: { document: {
    revision: 1, objects: [{ id, type: 'body' }],
  } }, scene: { revision: 1, objects: [] } });
  sandbox.window.cadyxModelSnapshot = snapshot('old');
  await sandbox.refresh();
  project = 'new';
  sandbox.window.cadyxModelSnapshot = snapshot('new');
  await sandbox.refresh();
  assert.equal(state.project, 'new', 'slug selection must load without project_id query');
  sandbox.window.cadyxModelSnapshot = snapshot('old');
  await sandbox.refresh();
  assert.equal(state.project, 'new', 'late old-project response must not replace current project');
  project = 'failed';
  sandbox.window.cadyxModelSnapshot = null;
  await sandbox.refresh();
  assert.equal(state.project, 'new', 'a missing snapshot must not commit invalid state');
  state.objects = [
    { id: 'stl', type: 'body' },
    { id: 'import', type: 'feature', body_id: 'stl', operation: 'import_stl' },
  ];
  vm.runInContext(`${groupsSource}\nglobalThis.groups = modelGroups;`, sandbox);
  assert.equal(sandbox.groups()[0].body.id, 'stl', 'imported STL must appear in the model tree');
  assert.match(treeSource, /disclosure\.innerHTML=`<span class="cad-tree-chevron">\$\{expanded\?'−':'\+'\}<\/span>`/, 'expanded sketch rows must show minus and collapsed rows plus');
  const feature = { id: 'last-feature', type: 'feature', body_id: 'result-body', operation: 'fillet_edges' };
  const selectionCalls = [];
  const selectionSandbox = {
    state: {
      byId: new Map([[feature.id, feature]]), objects: [feature],
      selectedOccurrenceId: null, selectedOccurrenceIds: new Set(),
      selectedInstanceId: null, selectedInstancePath: [],
      assemblyFacePicks: [], assemblyFacePickMode: false, selectionCleared: false,
    },
    modelGroups: () => [{ body: { id: 'result-body' }, features: [feature] }],
    modelViewer: {
      setSelectedFeature: id => selectionCalls.push(['feature', id]),
      setSelectedBody: id => selectionCalls.push(['body', id]),
    },
    bodyFor: () => ({ id: 'result-body' }), renderTree() {}, renderProps() {}, syncOverlay() {},
    detail: { dataset: {} }, list: null, CSS: { escape: value => value },
  };
  vm.createContext(selectionSandbox);
  vm.runInContext(`${selectionSource}\nglobalThis.select = select;`, selectionSandbox);
  selectionSandbox.select(feature.id);
  assert.deepEqual(selectionCalls, [['feature', feature.id]],
    'Selecting the last feature must highlight its own feature ranges instead of the whole result body');
  assert.match(selectionSource, /if\(selected\.type==='feature'\)modelViewer\?\.setSelectedFeature\?\.\(selected\.id\)/,
    'Every feature selection must be routed through feature-level viewport highlighting');
  console.log('Panel state: project switching, STL visibility and feature-level selection passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
