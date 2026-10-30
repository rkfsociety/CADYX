const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const code = source.slice(source.indexOf('    const scanRunPattern='), source.indexOf('    function activeScanPreviewMode(){'));
function element() {
  return { children: [], attrs: {}, hidden: false, textContent: '', innerHTML: '',
    append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
    setAttribute(k,v) { this.attrs[k]=v; }, querySelector() { return element(); }, click() { this.onclick?.(); } };
}
async function main() {
 const list=element(), events=[];
 const root={files:[{name:'combined_cloud_dense.ply',size:42}],folders:[{name:'Сшитые серии'}]};
 const staged={files:[{name:'Серия 1.ply',size:24},{name:'Серия 2.ply',size:25}]};
 const sandbox={state:{project:'p'}, list, search:{value:''}, scanTreeEntries:[], projectScanCloudFiles:[],
   stitchedSeriesFiles:[], scanTreeLoadedProject:null, scanTreeRequest:0, scanTreeExpanded:new Set(),
   scanTreePreviewSelection:null, localStorage:{getItem(){return null},setItem(){}},
   document:{createElement:element}, $:()=>null, Intl, Date, Map, Set, I:{cloud:'cloud'},
   semanticJson:async url=>url.includes('/folders/')?staged:root,
   previewScanFile:(run,file)=>events.push({run,file})};
 vm.createContext(sandbox);vm.runInContext(code+'\nglobalThis.load=loadScanTree;',sandbox);
 await sandbox.load('p');
 assert.equal(list.children.length,2);
 assert.match(list.children[0].textContent,/combined_cloud_dense/);
 const group=list.children[1], title=group.children[0].children[1];
 assert.equal(title.children[1].textContent,'Сшитые серии');
 assert.equal(title.children[1].className,'cad-tree-label');
 assert.equal(title.children[2].textContent,'2');
 assert.equal(group.children[1].hidden,true);
 group.children[0].children[0].click();
 const expanded=list.children[1];assert.equal(expanded.children[1].hidden,false);
 assert.equal(expanded.children[1].children.length,2);
 expanded.children[1].children[0].click();
 assert.equal(events[0].run.folderName,'Сшитые серии');assert.equal(events[0].file.name,'Серия 1.ply');
}
main().catch(error=>{console.error(error);process.exitCode=1});
