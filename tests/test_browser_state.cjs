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


async function checkViewportSnapshotSync() {
  const makeClock = () => {
    let time = 10000;
    const timers = [];
    return {
      now: () => time,
      timers,
      setTimer(callback, delay) {
        timers.push({ callback, delay });
        return timers.length;
      },
      async fire() {
        const next = timers.shift();
        assert.ok(next, 'expected a scheduled snapshot');
        time += next.delay;
        next.callback();
        for (let index = 0; index < 12; index += 1) await Promise.resolve();
        return next.delay;
      },
    };
  };
  const context = { projectId: 'p', renderedProjectId: 'p', revision: 1 };
  const clock = makeClock();
  const failures = [];
  let attempts = 0;
  const sync = state.createViewportSnapshotSynchronizer({
    now: clock.now, setTimer: clock.setTimer,
    getContext: () => context, capture: async () => 'frame',
    publish: async () => ({ ok: ++attempts === 3, status: attempts === 1 ? 503 : attempts === 2 ? 429 : 200 }),
    onFailure: (...args) => failures.push(args),
  });
  sync.markDirty();
  assert.equal(await clock.fire(), 0, 'the first snapshot should not wait for the throttle interval');
  assert.equal(await clock.fire(), 500, 'transient failure should retry after a bounded delay');
  assert.equal(await clock.fire(), 1000, 'a second transient failure should back off');
  assert.equal(attempts, 3);
  assert.equal(failures.length, 1, 'repeated failures should not spam the user');
  assert.equal(sync.state().dirty, false);
  assert.equal(sync.state().errorShown, false);
  sync.markDirty();
  assert.equal(await clock.fire(), 400, 'successful snapshots retain the normal throttle');

  const exhaustedClock = makeClock();
  let exhaustedAttempts = 0;
  const exhausted = state.createViewportSnapshotSynchronizer({
    now: exhaustedClock.now, setTimer: exhaustedClock.setTimer,
    getContext: () => context, capture: async () => 'frame',
    publish: async () => { exhaustedAttempts += 1; throw new TypeError('offline'); },
  });
  exhausted.markDirty();
  for (let index = 0; index < 4; index += 1) await exhaustedClock.fire();
  assert.equal(exhaustedAttempts, 4);
  assert.equal(exhaustedClock.timers.length, 0, 'persistent failures must not cause endless retries');
  assert.equal(exhausted.state().dirty, false);
  exhausted.markDirty();
  await exhaustedClock.fire();
  assert.equal(exhaustedAttempts, 5, 'a new viewport change should restart recovery');

  const conflictClock = makeClock();
  const conflict = state.createViewportSnapshotSynchronizer({
    now: conflictClock.now, setTimer: conflictClock.setTimer,
    getContext: () => context, capture: async () => 'frame',
    publish: async () => ({ ok: false, status: 409 }),
  });
  conflict.markDirty();
  await conflictClock.fire();
  assert.equal(conflictClock.timers.length, 0, 'revision conflicts must not be retried blindly');

  const changedClock = makeClock();
  let resolveCapture;
  let revision = 1;
  let captures = 0;
  const published = [];
  const changed = state.createViewportSnapshotSynchronizer({
    now: changedClock.now, setTimer: changedClock.setTimer,
    getContext: () => ({ projectId: 'p', renderedProjectId: 'p', revision }),
    capture: () => ++captures === 1 ? new Promise(resolve => { resolveCapture = resolve; }) : Promise.resolve('fresh'),
    publish: async snapshot => { published.push(snapshot); return { ok: true, status: 200 }; },
  });
  changed.markDirty();
  await changedClock.fire();
  revision = 2;
  resolveCapture('stale');
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(published.length, 0, 'a stale capture must never publish to the old revision');
  await changedClock.fire();
  assert.equal(published.length, 1);
  assert.equal(published[0].revision, 2);
  assert.equal(published[0].image, 'fresh');

  const stalePublishClock = makeClock();
  let resolveStalePublish;
  let activeRevision = 1;
  const sentRevisions = [];
  const staleFailures = [];
  const stalePublish = state.createViewportSnapshotSynchronizer({
    now: stalePublishClock.now, setTimer: stalePublishClock.setTimer,
    getContext: () => ({ projectId: 'p', renderedProjectId: 'p', revision: activeRevision }),
    capture: async () => 'frame',
    publish: snapshot => {
      sentRevisions.push(snapshot.revision);
      return sentRevisions.length === 1
        ? new Promise(resolve => { resolveStalePublish = resolve; })
        : Promise.resolve({ ok: true, status: 200 });
    },
    onFailure: (...args) => staleFailures.push(args),
  });
  stalePublish.markDirty();
  await stalePublishClock.fire();
  activeRevision = 2;
  resolveStalePublish({ ok: false, status: 503 });
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
  assert.equal(staleFailures.length, 0, 'obsolete publish responses must not show errors');
  assert.equal(await stalePublishClock.fire(), 0, 'new revision must not inherit old retry backoff');
  assert.deepEqual(sentRevisions, [1, 2], 'a revision change during publish must send a fresh frame');

  const invalidClock = makeClock();
  let invalidCaptures = 0;
  const invalid = state.createViewportSnapshotSynchronizer({
    now: invalidClock.now, setTimer: invalidClock.setTimer,
    getContext: () => ({ projectId: 'p', renderedProjectId: 'p', revision: -1 }),
    capture: async () => { invalidCaptures += 1; return 'invalid'; },
    publish: async () => ({ ok: true, status: 200 }),
  });
  invalid.markDirty();
  await invalidClock.fire();
  assert.equal(invalidCaptures, 0, 'invalid revisions must never be published');
  console.log('Viewport snapshots: immediate send, bounded retries, conflict and revision fencing passed.');
}

checkViewportSnapshotSync().catch(error => { console.error(error); process.exitCode = 1; });
