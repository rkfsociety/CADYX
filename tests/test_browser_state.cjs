const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/browser-state.js'), 'utf8');
const sandbox = { window: {} };
vm.runInNewContext(source, sandbox, { filename: 'browser-state.js' });
const state = sandbox.window.CadyxBrowserState;

const finished = { workflow: { progress_percent: 100, phases: [{ state: 'completed' }] }, tasks: [] };
assert.equal(state.dashboardHasUnfinishedWork(finished), false);
assert.equal(state.dashboardHasUnfinishedWork({ workflow: { progress_percent: 50, phases: [] } }), true);
assert.equal(state.dashboardHasUnfinishedWork({
  workflow: { progress_percent: 100, phases: [{ state: 'in_progress' }] },
}), true);
assert.equal(state.dashboardHasUnfinishedWork({ tasks: [{ state: 'running' }] }), true);
assert.equal(state.dashboardHasUnfinishedWork({ workflow: { progress_percent: 100 } }), false);
assert.equal(state.dashboardHasUnfinishedWork({ workflow: { progress_percent: 100, phases: {} }, tasks: {} }), false);
assert.equal(state.dashboardHasUnfinishedWork({ workflow: { progress_percent: 100, phases: [null] } }), true);

const idle = state.currentProgressWork({ tasks: {}, workflow: { phases: {} } }, [null, false, 'bad']);
assert.equal(idle.isWorking, false);
assert.equal(idle.activity, null);
assert.equal(idle.activities.length, 0);

const active = state.currentProgressWork({
  tasks: [null, { state: 'running', title: 'Task' }],
  workflow: { phases: [null, { state: 'in_progress', title: 'Phase' }] },
}, [null, { state: 'running', action: 'Building' }]);
assert.equal(active.isWorking, true);
assert.equal(active.runningTask.title, 'Task');
assert.equal(active.activePhase.title, 'Phase');
assert.equal(active.runningActivity.action, 'Building');
assert.equal(active.activities.length, 1);

const locked = state.currentProgressWork(null, null, {
  state: 'AI_LOCKED', lease: { owner_type: 'agent' }, recovery_required: false,
});
assert.equal(locked.agentLeaseActive, true);
assert.equal(locked.isWorking, true);
console.log('Browser state: malformed progress payloads and normal active work passed.');
