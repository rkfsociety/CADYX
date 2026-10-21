const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf('(() => {')), context);
const stage = feature => vm.runInContext(`cadyxCloudStage(${JSON.stringify(feature)})`, context);
assert.equal(stage({operation:'import_point_cloud',stats:{point_count:221656}}).output, 221656);
const filtered = stage({operation:'process_point_cloud',method:'radius_filter',stats:{input_points:221656,output_points:221502}});
assert.equal(filtered.title, 'Фильтр изолированных точек');
assert.equal(filtered.input - filtered.output, 154);
assert.equal(stage({operation:'extrude_sketch'}), null);
const meshStage = feature => vm.runInContext(`cadyxMeshStage(${JSON.stringify(feature)})`, context);
assert.equal(meshStage({operation:'process_mesh',method:'smooth',stats:{before:{triangles:100},after:{triangles:100}}}).title, 'Сглаживание сетки');
assert.equal(meshStage({operation:'process_mesh',method:'remove_small_components',stats:{before:{triangles:100},after:{triangles:80}}}).output, 80);
assert.equal(meshStage({operation:'import_point_cloud'}), null);
assert.ok(!source.includes('dataset.cloudTool'), 'Unused agent tools must not be rendered in the browser tree');
assert.deepEqual(Array.from(vm.runInContext('Object.keys(cadyxCloudTools)', context)), ['deduplicate','radius_filter','crop','voxel_downsample','remove_overlaps','isolation_filter','simplify','smooth']);
assert.equal(stage({operation:'process_point_cloud',method:'isolation_filter',stats:{input_points:100,output_points:85}}).title, 'Удаление изолированных точек');
assert.equal(stage({operation:'process_point_cloud',method:'simplify',stats:{input_points:100,output_points:60}}).title, 'Упрощение облака точек');
assert.equal(stage({operation:'process_point_cloud',method:'remove_overlaps',stats:{input_points:100,output_points:50}}).title, 'Удаление совпадающих слоёв');
assert.equal(stage({operation:'process_point_cloud',method:'smooth',stats:{input_points:100,output_points:100}}).title, 'Сглаживание облака точек');
const groupStart = source.indexOf('function modelGroups(){');
const groupEnd = source.indexOf('function renderTree(){', groupStart);
context.state = {objects:[
  {id:'body:original',type:'body'}, {id:'body:result',type:'body'},
  {id:'feature:import',type:'feature',body_id:'body:original',operation:'reconstruct_scan'},
  {id:'feature:smooth',type:'feature',body_id:'body:result',source_body_id:'body:original',operation:'process_mesh'},
  {id:'feature:recognition',type:'feature',body_id:'body:result',operation:'recognize_mesh'},
]};
vm.runInContext(source.slice(groupStart,groupEnd),context);
assert.deepEqual(Array.from(vm.runInContext('modelGroups()[0].features.map(f=>f.id)',context)),
  ['feature:import','feature:smooth','feature:recognition'], 'Recognition preserves the full preceding mesh-processing tree');
console.log('Point-cloud, recognition and processing-tree presentation checks passed.');
