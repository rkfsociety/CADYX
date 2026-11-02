const assert = require('node:assert/strict');
const {summary} = require('../web/scan-progress.js');
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
