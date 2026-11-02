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
