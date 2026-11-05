const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const code = source.slice(source.indexOf('    const scanRunPattern='), source.indexOf('    function activeScanPreviewMode(){'));
function element() {
  return {children:[], attrs:{}, dataset:{}, hidden:false, textContent:'', innerHTML:'',
    append(...items){this.children.push(...items)}, replaceChildren(){this.children=[]},
    setAttribute(k,v){this.attrs[k]=v}, querySelector(){return element()}, click(){return this.onclick?.()}};
}
async function main(){
  const id='revopoint_20261010T100000Z', list=element(), requests=[];
  const sandbox={state:{project:'p'},list,search:{value:''},scanTreeEntries:[],projectScanCloudFiles:[],
    stitchedSeriesFiles:[],scanTreeLoadedProject:null,scanTreeRequest:0,scanTreeExpanded:new Set(),
    scanTreePreviewSelection:null,scanFusionJob:null,scanFusionPending:false,
    localStorage:{getItem(){return null},setItem(){}},document:{createElement:element},$:()=>null,
    Intl,Date,Map,Set,I:{cloud:'cloud'},canOpenFilesView:()=>true,notify:()=>{},window:{confirm:()=>true},
    semanticJson:async url=>url.includes('/folders/')?{files:[{name:`${id}_pose_000.ply`,size:20}]}:
      {files:[],folders:[{name:id}]},
    fetch:async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({fusion:{state:'running',run_id:id}})}}};
  vm.createContext(sandbox);vm.runInContext(code+'\nglobalThis.load=loadScanTree;',sandbox);
  await sandbox.load('p');
  await list.children[0].children[0].children[0].onclick();
  const button=list.children[0].children[1].children[0];
  assert.equal(button.textContent,'Сшить и обработать');
  await button.click();
  assert.match(requests[0].url,/\/scans\/fusion$/);
  assert.deepEqual(JSON.parse(requests[0].options.body),{run_id:id});
  const busyButton=list.children[0].children[1].children[0];
  assert.equal(busyButton.disabled,true);
  assert.equal(busyButton.textContent,'Сшивка выполняется…');
  const stage = sandbox.scanTreeEntries[0];
  stage.files.push({name:`${id}_fused.cadyx-abcdef123456.ply`,size:80}, {name:`${id}_cleaned.cadyx-abcdef123456.ply`,size:60});
  vm.runInContext('renderScanTree()',sandbox);
  const names=list.children[0].children[1].children.flatMap(item=>item.children?.length?item.children.map(child=>child.textContent):[item.textContent]);
  assert.ok(names.some(name=>name.includes('Сшитое облако')));
  assert.ok(names.some(name=>name.includes('Очищенное облако')));
  vm.runInContext('scanFusionJob=null; renderScanTree()',sandbox);
  const rescan=list.children[0].children[1].children[1];
  assert.equal(rescan.textContent,'Удалить и переснять');
  await rescan.click();
  assert.match(requests[1].url,/\/scans\/360$/);
  assert.equal(JSON.parse(requests[1].options.body).replace_run_id,id);
  assert.equal(JSON.parse(requests[1].options.body).step_degrees,15);

}
main().catch(error=>{console.error(error);process.exitCode=1});
