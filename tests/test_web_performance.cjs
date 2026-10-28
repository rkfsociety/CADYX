const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workspace = fs.readFileSync(path.join(__dirname, '../web/ply-preview.js'), 'utf8').replace(/\r\n/g, '\n');
const restore = workspace.slice(workspace.indexOf('try {\n  const savedCamera'), workspace.indexOf('const emptyViewportGeometry'));
for (const type of ['navigate', 'reload']) {
  const replacements = [];
  const sandbox = {
    reloadCameraStorageKey: 'camera', restorableWorkspaceViews: new Set(['geometry', 'analysis']),
    URL, performance: { getEntriesByType: () => [{ type }] },
    sessionStorage: {
      getItem: () => JSON.stringify({ camera: { zoom: 1 }, workspaceView: 'geometry' }),
      removeItem() {},
    },
    window: { location: new URL('http://localhost/p/part?view=analysis'),
      history: { replaceState: (_state, _title, url) => replacements.push(url) } },
  };
  vm.runInNewContext(restore, sandbox);
  assert.equal(replacements.length, type === 'reload' ? 1 : 0,
    'saved view restoration must not replace an explicit navigation to another section');
}

const renderProjects = workspace.slice(workspace.indexOf('function renderProjects()'), workspace.indexOf('function showProjectLoading'));
vm.runInNewContext(`${renderProjects}\nrenderProjects();`, {
  primaryGrid: { dataset: { view: 'geometry' } },
  $: () => { throw Error('hidden project cards must not construct thumbnails'); },
});

console.log('Web performance: explicit navigation and hidden project thumbnails passed.');
