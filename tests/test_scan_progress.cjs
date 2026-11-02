const assert = require('node:assert/strict');
const {summary, render} = require('../web/scan-progress.js');
assert.equal(summary({state: 'idle'}), null);
const running = summary({state: 'running', frame_count: 12, completed_frames: 12, preprocessed_frames: 5,
  preprocessing_status: 'running', preprocessing_current_frame: {frame_index: 5, source_file: 'frame_005.ply'}});
assert.equal(running.title, 'Обработка кадров');
assert.match(running.detail, /frame_005\.ply/);
assert.equal(running.cleaned, 5);
assert.equal(summary({state:'running', frame_count:12, completed_frames:4, preprocessed_frames:1,
  preprocessing_status:'running'}).detail, 'Очистка кадров выполняется');
assert.equal(summary({state:'failed', frame_count:12, completed_frames:6, message:'Ошибка связи'}).detail, 'Ошибка связи');
assert.equal(summary({state:'completed', frame_count:12, completed_frames:12, preprocessed_frames:12}).title, 'Съёмка и очистка завершены');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
}
global.document = {createElement: () => new Element()};
const container = new Element();
render(container, {state:'running', frame_count:12, completed_frames:12, preprocessed_frames:5,
  preprocessing_status:'running', preprocessing_current_frame:{frame_index:5, source_file:'frame_005.ply'}});
assert.equal(container.children.length, 5);
assert.equal(container.children[0].dataset.stageState, 'completed');
assert.equal(container.children[1].dataset.stageState, 'active');
assert.equal(container.children[2].dataset.stageState, 'unknown');
assert.equal(container.children[3].dataset.stageState, 'unknown');
assert.equal(container.children[4].children[1].textContent, 'frame_005.ply');
render(container, {state:'failed', frame_count:12, completed_frames:6, preprocessed_frames:5,
  preprocessing_status:'failed', preprocessing_failed_frames:1, message:'Ошибка'});
assert.equal(container.children[0].dataset.stageState, 'failed');
assert.equal(container.children[1].dataset.stageState, 'failed');
render(container, {state:'busy', frame_count:12});
assert.equal(container.children[0].dataset.stageState, 'unknown');
assert.equal(container.children[1].dataset.stageState, 'unknown');

const capture = {state:'completed', frame_count:12, completed_frames:12, preprocessed_frames:12,
  preprocessing_status:'completed', preview_subfolder:'revopoint_20261010T100000Z'};
const job = {state:'running', run_id:capture.preview_subfolder, phase:'register', done:3, total:11, file:'frame_004.ply'};
render(container, capture, job);
assert.equal(container.children[2].dataset.stageState, 'active');
assert.equal(container.children[2].children[1].children[0].children[1].textContent, '3 / 11');
assert.equal(container.children[3].dataset.stageState, 'waiting');
assert.equal(container.children[4].children[1].textContent, 'frame_004.ply');
render(container, capture, {...job, phase:'cleanup', done:1, total:2});
assert.equal(container.children[2].dataset.stageState, 'completed');
assert.equal(container.children[3].dataset.stageState, 'active');
render(container, capture, {...job, state:'failed', phase:'cleanup', error:'Ошибка очистки'});
assert.equal(container.children[3].dataset.stageState, 'failed');
assert.equal(container.children[4].children[2].textContent, 'Ошибка очистки');
render(container, capture, {...job, state:'completed', phase:'completed', result:{cleaned:'result.ply'}});
assert.equal(container.children[3].dataset.stageState, 'completed');
assert.equal(container.children[4].children[2].textContent, 'Результат предварительный');
render(container, capture, {...job, run_id:'another-series'});
assert.equal(container.children[0].dataset.stageState, 'unknown');

render(container, capture, {...job, state:'completed', phase:'completed', result:{cleaned:'result.ply', quality:'review_required'}});
assert.equal(container.children[2].dataset.stageState, 'warning');
assert.match(container.children[4].children[2].textContent, /проверьте совмещение/);

render(container, capture, {...job, state:'failed', phase:'register', error:'geometry', next_action:'Повторите съёмку в том же положении.'});
assert.equal(container.children[4].children[2].textContent, 'Повторите съёмку в том же положении.');

// An absent scan without a fusion job still hides the progress panel.
render(container, null);
assert.equal(container.hidden, true);
assert.equal(container.children.length, 0);

// Fusion polling can lead the capture snapshot, including on first load or after an SSE gap.
render(container, null, job);
assert.equal(container.hidden, false);
assert.equal(container.children.length, 5);
assert.equal(container.children[0].dataset.stageState, 'unknown');
assert.equal(container.children[2].dataset.stageState, 'active');
assert.equal(container.children[2].children[1].children[0].children[1].textContent, '3 / 11');
render(container, undefined, {...job, phase:'cleanup', done:1, total:2});
assert.equal(container.children[3].dataset.stageState, 'active');

// Partial or corrupt progress payloads must not leak impossible numbers into the UI.
for (const [done, expected] of [[99, '11 / 11'], [-5, '0 / 11'], [null, '0 / 11'], [2.5, '0 / 11']]) {
  render(container, capture, {...job, done});
  assert.equal(container.children[2].children[1].children[0].children[1].textContent, expected);
  const bar = container.children[2].children[1].children[1];
  assert.ok(bar.value >= 0 && bar.value <= bar.max);
}
render(container, capture, {...job, total:0, done:4});
assert.equal(container.children[2].children[1].children[0].children[1].textContent, '—');
