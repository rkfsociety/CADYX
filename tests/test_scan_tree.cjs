const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const code = source.slice(source.indexOf('    const scanRunPattern='), source.indexOf('    function activeScanPreviewMode(){'))
  + source.slice(source.indexOf('    async function deleteStitchedCloud(file){'), source.indexOf('    function renderAssemblyTree(){'));
function element() {
  return { children: [], attrs: {}, hidden: false, textContent: '', innerHTML: '',
    append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
    setAttribute(k,v) { this.attrs[k]=v; }, querySelector() { return element(); }, click(event={stopPropagation(){}}) { return this.onclick?.(event); } };
}
async function main() {
 const list=element(), events=[], requests=[];
 const runId='revopoint_20261105T070857Z';
 const root={files:[{name:'combined_cloud_dense.ply',size:42}],folders:[{name:'Сшитые серии'},{name:runId}]};
 const staged={files:[{name:'Серия 1.ply',size:24},{name:'Серия 2.ply',size:25}]};
 const runFiles={files:[{name:`${runId}_pose_000.ply`,size:10},{name:`${runId}_all_frames_fused.ply`,size:20}],folders:[{name:'depth_frames'}]};
 const depthFrames={files:[{name:'frame_00074.raw',size:100},{name:'frame_00075.raw',size:100},{name:'frame_00076.raw',size:100}]};
 const sandbox={state:{project:'p'}, list, search:{value:''}, scanTreeEntries:[], projectScanCloudFiles:[],
   stitchedSeriesFiles:[], scanTreeLoadedProject:null, scanTreeRequest:0, scanTreeExpanded:new Set(),
   scanTreePreviewSelection:null, scanFusionJob:null, scanFusionPending:false, localStorage:{getItem(){return null},setItem(){}},
   document:{createElement:element}, $:()=>null, Intl, Date, Map, Set, I:{cloud:'cloud'},
   semanticJson:async url=>url.includes('depth_frames')?depthFrames:url.includes(`/${runId}/files`)?runFiles:url.includes('/folders/')?staged:root,
   fetch:async()=>({ok:true,text:async()=>'index,filename\n0,frame_00074.raw\n1,frame_00076.raw\n'}),
   previewScanFile:(run,file)=>events.push({run,file}),
   canOpenFilesView:()=>true,window:{confirm:()=>true},notify:()=>{},clearScanTreePreview:()=>{},
   api:async(url,options)=>{requests.push({url,options});return{}}};
 vm.createContext(sandbox);vm.runInContext(code+'\nglobalThis.load=loadScanTree;',sandbox);
 await sandbox.load('p');
 assert.equal(list.children.length,3);
 assert.match(list.children[0].children[0].textContent,/combined_cloud_dense/);
 const group=list.children[1], title=group.children[0].children[1];
 assert.equal(title.children[1].textContent,'Сшитые серии');
 assert.equal(title.children[1].className,'cad-tree-label');
 assert.equal(title.children[2].textContent,'2');
 assert.equal(group.children[1].hidden,true);
 group.children[0].children[0].click();
 const expanded=list.children[1];assert.equal(expanded.children[1].hidden,false);
 assert.equal(expanded.children[1].children.length,2);
 expanded.children[1].children[0].children[0].click();
 assert.equal(events[0].run.folderName,'Сшитые серии');assert.equal(events[0].file.name,'Серия 1.ply');
 await expanded.children[1].children[0].children[1].click();
 assert.match(decodeURIComponent(requests[0].url),/\/folders\/Сшитые серии\/files\/delete$/);
 assert.deepEqual(JSON.parse(requests[0].options.body),{filenames:['Серия 1.ply']});
 assert.equal(requests.length,1);
 await list.children[0].children[1].click();
 assert.match(requests[1].url,/\/projects\/p\/files\/delete$/);
 assert.deepEqual(JSON.parse(requests[1].options.body),{filenames:['combined_cloud_dense.ply']});
 const scan=()=>list.children.find(node=>node.children[0]?.className==='cad3-tree-parent-row cad3-scan-run-row');
 await scan().children[0].children[0].click();
 const scanChildren=()=>scan().children.find(child=>child.className==='cad3-tree-children');
 assert.equal(scanChildren().hidden,false);
const frameToggle=scanChildren().children.find(child=>child.className==='cad3-scan-frame-group').children[0];
 assert.equal(frameToggle.children[0].textContent,'Кадры непрерывной съёмки');
 assert.equal(frameToggle.children[1].textContent,'2');
 const cloudRows=scanChildren().children.find(child=>child.className==='cad3-scan-run-files').children;
 assert.equal(cloudRows.length,1);
 assert.match(cloudRows[0].textContent,/all_frames_fused/);
 assert.doesNotMatch(cloudRows.map(child=>child.textContent).join(' '),/pose_000/);
 frameToggle.click();
 const frames=scanChildren().children.find(child=>child.className==='cad3-scan-frame-group').children[1];
 assert.equal(frames.children.length,2);
 assert.match(frames.children[0].textContent,/Кадр 00074/);
 assert.match(frames.children[1].textContent,/Кадр 00076/);
}
main().catch(error=>{console.error(error);process.exitCode=1});
