const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const start = source.indexOf("    const toolbar=$('.model-toolbar')");
const end = source.indexOf('    const scanView=', start);
assert.ok(start >= 0 && end > start);
const elements = new Map();
for (const selector of ['.model-toolbar','#viewport-camera-presets','#viewport-fit','#viewport-four-views',
  '.viewport-toolbar-divider','#viewport-live-scan','#viewport-scan-mesh','#viewport-live-camera',
  '#viewport-toggle-turntable','#viewport','#viewport-scan-360','#viewport-scan-360-status']) {
  elements.set(selector, {classList:{add(){}},replaceChildren(...items){this.children=items}});
}
const sandbox={$:selector=>elements.get(selector),detail:{dataset:{}},I:{fit:'icon'},
  URLSearchParams,location:{search:''}};
vm.runInNewContext(source.slice(start,end),sandbox);
assert.ok(elements.get('.model-toolbar').children.includes(elements.get('#viewport-toggle-turntable')),
  'the assembled scanner toolbar keeps the table visibility button');
